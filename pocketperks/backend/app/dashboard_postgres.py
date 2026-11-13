"""Native PostgreSQL dashboard aggregates with the existing response contract."""
from fastapi import HTTPException


async def get_dashboard_run_detail(pool, run_id: str) -> dict:
    async with pool.acquire() as conn:
        row=await conn.fetchrow("""select r.id,r.campaign_id,r.batch_id,r.status,r.total_leads,r.processed,r.skipped,r.failed,
                r.created_at,r.finished_at,c.name,
                coalesce(m.status,'not_started') sync_status,m.error_detail sync_error,
                count(pe.id) filter(where pe.status='queued' and lm.status='synced') queued_emails,
                count(pe.id) filter(where pe.status='queued' and coalesce(lm.status,'')<>'synced') not_added_emails,
                count(pe.id) filter(where pe.status in ('sent','sent_test')) sent_emails,
                count(pe.id) filter(where pe.status='failed') failed_emails,
                count(pe.id) filter(where pe.status='pending_review') pending_review_emails,
                count(pe.id) filter(where pe.status='bounced') bounced_emails,
                count(pe.id) filter(where pe.status='cancelled') cancelled_after_bounce
            from public.campaign_runs r join public.campaigns c on c.id=r.campaign_id
            left join public.instantly_campaign_mappings m on m.run_id=r.id
            left join public.personalized_emails pe on pe.run_id=r.id
            left join public.instantly_lead_mappings lm on lm.campaign_mapping_id=m.id and lm.lead_id=pe.lead_id
            where r.id=$1::uuid group by r.id,c.name,m.status,m.error_detail""",run_id)
    if not row: raise HTTPException(status_code=404,detail="Campaign activity was not found.")
    d=dict(row)
    return {"run_id":str(d["id"]),"campaign_id":str(d["campaign_id"]),"batch_id":str(d["batch_id"]),
        "campaign_name":d["name"],"status":d["status"],"total_leads":d["total_leads"],"processed":d["processed"],
        "skipped":d["skipped"],"failed_leads":d["failed"],"created_at":d["created_at"],"finished_at":d["finished_at"],
        **{k:int(d[k] or 0) for k in ("queued_emails","not_added_emails","sent_emails","failed_emails","pending_review_emails","bounced_emails","cancelled_after_bounce")},
        "sync_status":d["sync_status"],"sync_error":d["sync_error"]}


async def get_dashboard_summary(pool, settings, *, activity_days: int=14) -> dict:
    async with pool.acquire() as conn:
        counts=await conn.fetchrow("""select
            (select count(*) from public.leads) total_leads,
            (select count(*) from public.campaigns) total_campaigns,
            (select count(*) from public.campaigns where status='approved') approved_campaigns,
            (select count(*) from public.personalized_emails where status='pending_review') pending_approvals,
            (select count(*) from public.campaign_runs where status='running') active_runs,
            (select count(*) from public.replies) total_replies,
            (select count(*) from public.replies where status='new') unread_replies,
            (select count(*) from public.instantly_campaign_mappings where instantly_campaign_id is not null) instantly_campaigns,
            (select count(*) from public.instantly_lead_mappings where status='synced') instantly_leads_synced,
            (select count(*) from public.instantly_campaign_mappings where status='active') instantly_active_campaigns,
            (select count(*) from public.instantly_campaign_mappings where status='completed') instantly_completed_campaigns,
            (select count(*) from public.personalized_emails where status='queued' and exists(select 1 from public.instantly_campaign_mappings cm join public.instantly_lead_mappings lm on lm.campaign_mapping_id=cm.id and lm.lead_id=personalized_emails.lead_id and lm.status='synced' where cm.run_id=personalized_emails.run_id)) queued_not_sent,
            (select count(*) from public.personalized_emails where status='queued' and not exists(select 1 from public.instantly_campaign_mappings cm join public.instantly_lead_mappings lm on lm.campaign_mapping_id=cm.id and lm.lead_id=personalized_emails.lead_id and lm.status='synced' where cm.run_id=personalized_emails.run_id)) not_added_emails""")
        activity=await conn.fetch("""with days as(select generate_series(current_date-($1::int-1),current_date,'1 day')::date d),
            l as(select created_at::date d,count(*) n from public.leads where created_at>=now()-make_interval(days=>$1) group by 1),
            r as(select received_at::date d,count(*) n from public.replies where received_at>=now()-make_interval(days=>$1) group by 1),
            e as(select coalesce(sent_at,created_at)::date d,count(*) filter(where status in('sent','sent_test')) sent,
                count(*) filter(where status='queued' and exists(select 1 from public.instantly_campaign_mappings cm join public.instantly_lead_mappings lm on lm.campaign_mapping_id=cm.id and lm.lead_id=pe.lead_id and lm.status='synced' where cm.run_id=pe.run_id)) queued,
                count(*) filter(where status='queued' and not exists(select 1 from public.instantly_campaign_mappings cm join public.instantly_lead_mappings lm on lm.campaign_mapping_id=cm.id and lm.lead_id=pe.lead_id and lm.status='synced' where cm.run_id=pe.run_id)) not_added
                from public.personalized_emails pe where coalesce(sent_at,created_at)>=now()-make_interval(days=>$1) group by 1),
            b as(select event_at::date d,count(*) n from public.instantly_bounce_events where event_at>=now()-make_interval(days=>$1) group by 1)
            select days.d,coalesce(l.n,0) leads,coalesce(r.n,0) replies,coalesce(e.sent,0) sent,
              coalesce(e.queued,0) queued,coalesce(e.not_added,0) not_added,coalesce(b.n,0) bounced
              from days left join l using(d) left join r using(d) left join e using(d) left join b using(d) order by d""",activity_days)
        statuses=await conn.fetch("select status,count(*)::int n from public.campaigns group by status")
        email_status=await conn.fetch("select status,count(*)::int n from public.personalized_emails group by status")
        recent=await conn.fetch("""select r.id,r.campaign_id,c.name,r.status,r.total_leads,r.created_at,
            coalesce(m.status,'not_started') sync_status,m.error_detail sync_error
            from public.campaign_runs r join public.campaigns c on c.id=r.campaign_id
            left join public.instantly_campaign_mappings m on m.run_id=r.id order by r.created_at desc,r.id desc limit 12""")
        bounce=await conn.fetch("""select b.event_key,b.email,b.bounce_type,b.reason,b.step_number,b.email_account,b.event_at,b.match_status,
            l.business_name,c.name campaign_name from public.instantly_bounce_events b
            left join public.leads l on l.id=b.lead_id left join public.campaigns c on c.id=b.campaign_id
            order by b.event_at desc limit 10""")
        bounce_counts=await conn.fetchrow("select count(*) total,count(*) filter(where bounce_type='hard') hard,count(*) filter(where bounce_type='soft') soft,count(*) filter(where bounce_type='unknown') unknown from public.instantly_bounce_events")
        completed=await conn.fetch("""select c.name campaign_name,m.updated_at completed_at from public.instantly_campaign_mappings m
            join public.campaign_runs r on r.id=m.run_id join public.campaigns c on c.id=r.campaign_id
            where m.status='completed' order by m.updated_at desc limit 5""")
        latest_row=recent[0] if recent else None
    recent_runs=[{"run_id":str(x["id"]),"campaign_id":str(x["campaign_id"]),"campaign_name":x["name"],
        "status":x["status"],"total_leads":x["total_leads"],"created_at":x["created_at"],
        "sync_status":x["sync_status"],"sync_error":x["sync_error"]} for x in recent]
    activity_json={"days":[{"date":x["d"],"leads":x["leads"],"replies":x["replies"],"sent":x["sent"],"queued":x["queued"],"not_added":x["not_added"],"bounced":x["bounced"]} for x in activity],
        "campaign_status":[{"status":x["status"],"count":x["n"]} for x in statuses],
        "email_status":[{"status":x["status"],"count":x["n"]} for x in email_status],
        "delivery_totals":{"queued":counts["queued_not_sent"],"not_added":counts["not_added_emails"]}}
    latest=None
    if latest_row: latest=await get_dashboard_run_detail(pool,str(latest_row["id"]))
    return {**dict(counts),"total_leads":counts["total_leads"],"total_campaigns":counts["total_campaigns"],
        "approved_campaigns":counts["approved_campaigns"],"pending_approvals":counts["pending_approvals"],
        "queued_not_sent":counts["queued_not_sent"],"not_added_emails":counts["not_added_emails"],
        "active_runs":counts["active_runs"],"total_replies":counts["total_replies"],"unread_replies":counts["unread_replies"],
        "instantly_campaigns":counts["instantly_campaigns"],"instantly_leads_synced":counts["instantly_leads_synced"],
        "instantly_active_campaigns":counts["instantly_active_campaigns"],"instantly_completed_campaigns":counts["instantly_completed_campaigns"],
        "instantly_auto_launch_enabled":settings.instantly_auto_launch and settings.send_mode=='live',
        "activity":activity_json,"latest_run":latest,"recent_runs":recent_runs,
        "recent_completed_campaigns":[dict(x) for x in completed],
        "bounce_activity":{"available":True,"total":bounce_counts["total"],"hard":bounce_counts["hard"],"soft":bounce_counts["soft"],"unknown":bounce_counts["unknown"],"recent":[dict(x) for x in bounce]}}
