"use client";

import { apiFetch } from "../../lib/auth";

import { useCallback, useEffect, useState } from "react";

type RunDetail = {
  run_id: string;
  campaign_id: string;
  campaign_name: string;
  status: string;
  total_leads: number;
  processed: number;
  skipped: number;
  failed_leads: number;
  queued_emails: number;
  sent_emails: number;
  failed_emails: number;
  pending_review_emails: number;
  bounced_emails: number;
  cancelled_after_bounce: number;
  created_at: string;
  finished_at?: string | null;
};

type Summary = {
  total_leads: number;
  total_campaigns: number;
  approved_campaigns: number;
  pending_approvals: number;
  queued_not_sent: number;
  active_runs: number;
  total_replies: number;
  unread_replies: number;
  instantly_campaigns: number;
  instantly_leads_synced: number;
  instantly_active_campaigns: number;
  instantly_completed_campaigns: number;
  instantly_auto_launch_enabled: boolean;
  recent_completed_campaigns: { campaign_name: string; completed_at: string }[];
  latest_run: RunDetail | null;
  recent_runs: { run_id: string; campaign_name: string; status: string; total_leads: number; created_at: string }[];
  activity: {
    available?: boolean;
    days: { date: string; leads: number; replies: number }[];
    campaign_status: { status: string; count: number }[];
    email_status: { status: string; count: number }[];
  };
  bounce_activity: {
    available: boolean;
    total: number;
    hard: number;
    soft: number;
    unknown: number;
    recent: {
      event_key: string;
      email?: string | null;
      bounce_type: "hard" | "soft" | "unknown";
      reason?: string | null;
      step_number?: number | null;
      email_account?: string | null;
      event_at: string;
      match_status: "matched" | "unmatched";
      business_name?: string | null;
      campaign_name?: string | null;
    }[];
  };
};

type Props = {
  apiBase: string;
  refreshKey: number;
  onNavigate: (page: "Campaigns" | "Human Approval" | "Lead Replies") => void;
};

export default function DashboardSummary({ apiBase, refreshKey, onNavigate }: Props) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activityDays, setActivityDays] = useState(14);
  const [selectedEmailStatus, setSelectedEmailStatus] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedRunDetail, setSelectedRunDetail] = useState<RunDetail | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [runDetailError, setRunDetailError] = useState("");

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/dashboard/summary?days=${activityDays}`);
      const data = await response.json() as Summary & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load dashboard totals.");
      setSummary(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load dashboard totals.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, activityDays]);

  useEffect(() => {
    void loadSummary();
    const timer = window.setInterval(() => void loadSummary(), 60_000);
    return () => window.clearInterval(timer);
  }, [loadSummary, refreshKey]);

  useEffect(() => {
    const runs = summary?.recent_runs || [];
    if (!runs.length) {
      setSelectedRunId("");
      setSelectedRunDetail(null);
      return;
    }
    if (!selectedRunId || !runs.some(run => run.run_id === selectedRunId)) {
      setSelectedRunId(runs[0].run_id);
    }
  }, [summary, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId || !summary) return;
    if (summary.latest_run?.run_id === selectedRunId) {
      setSelectedRunDetail(summary.latest_run);
      setRunDetailError("");
      return;
    }
    let cancelled = false;
    setRunDetailLoading(true);
    setRunDetailError("");
    void apiFetch(`${apiBase}/api/dashboard/runs/${selectedRunId}`).then(async response => {
      const data = await response.json() as RunDetail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load this campaign activity.");
      if (!cancelled) setSelectedRunDetail(data);
    }).catch(cause => {
      if (!cancelled) setRunDetailError(cause instanceof Error ? cause.message : "Could not load this campaign activity.");
    }).finally(() => { if (!cancelled) setRunDetailLoading(false); });
    return () => { cancelled = true; };
  }, [apiBase, selectedRunId, summary]);

  const cards = summary ? [
    { label: "Total leads", value: summary.total_leads, hint: "Imported and available", tone: "violet" },
    { label: "Campaigns", value: summary.total_campaigns, hint: `${summary.approved_campaigns} approved`, tone: "blue" },
    { label: "Needs approval", value: summary.pending_approvals, hint: "Human review required", tone: "amber" },
    { label: "Waiting to send", value: summary.queued_not_sent, hint: "No delivery confirmation yet", tone: "amber" },
    { label: "Bounce events", value: summary.bounce_activity?.total || 0, hint: summary.bounce_activity?.available === false ? "Details unavailable" : `${summary.bounce_activity?.hard || 0} hard · ${summary.bounce_activity?.soft || 0} temporary · ${summary.bounce_activity?.unknown || 0} need review`, tone: "amber" },
    { label: "Emails being prepared", value: summary.active_runs, hint: "Currently processing", tone: "blue" },
    { label: "Sending campaigns", value: summary.instantly_active_campaigns, hint: `${summary.instantly_completed_campaigns} completed${summary.recent_completed_campaigns?.length ? ` · latest: ${summary.recent_completed_campaigns[0].campaign_name}` : ""}${summary.instantly_auto_launch_enabled ? " · automatic start on" : " · automatic start is off"}`, tone: "violet" },
    { label: "Leads added to campaigns", value: summary.instantly_leads_synced, hint: "Ready for scheduled sending", tone: "green" },
    { label: "Lead replies", value: summary.total_replies, hint: `${summary.unread_replies} unread in PocketPerks`, tone: "amber" },
  ] : [];
  const activity = summary?.activity;
  const points = activity?.days || [];
  const plot = { left: 42, top: 18, width: 650, height: 148 };
  const peak = Math.max(1, ...points.map(point => Math.max(point.leads, point.replies)));
  const seriesPoints = (field: "leads" | "replies") => points.map((point, index) => {
    const x = plot.left + (points.length <= 1 ? plot.width / 2 : index * plot.width / (points.length - 1));
    const y = plot.top + plot.height - (point[field] / peak) * plot.height;
    return { x, y, value: point[field] };
  });
  const leadSeries = seriesPoints("leads");
  const replySeries = seriesPoints("replies");
  const emailStatusColors: Record<string, string> = {
    generated: "#aab5ae", pending_review: "#d5a23d", approved: "#7390d4", queued: "#d5a23d",
    sent: "#16843c", sent_test: "#6eaecb", failed: "#cc5552", rejected: "#934c73",
    bounced: "#bd3d3d", cancelled: "#87938c",
  };
  const emailStatuses = activity?.email_status?.filter(item => item.count > 0) || [];
  const emailStatusTotal = emailStatuses.reduce((total, item) => total + item.count, 0);
  const ringLength = 2 * Math.PI * 42;
  let ringOffset = 0;
  const ringSegments = emailStatuses.map(item => {
    const length = emailStatusTotal ? (item.count / emailStatusTotal) * ringLength : 0;
    const segment = { ...item, length, offset: ringOffset, color: emailStatusColors[item.status] || "#7b8c83" };
    ringOffset += length;
    return segment;
  });
  const formatStatus = (value: string) => ({
    queued: "Waiting to send",
    pending_review: "Needs review",
    generated: "Draft ready",
    sent_test: "Test sent",
    failed: "Could not send",
    bounced: "Bounced",
    cancelled: "Stopped after bounce",
  }[value] || value.replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase()));

  return (
    <section className="dashboard-summary" aria-labelledby="dashboard-heading">
      <div className="dashboard-heading">
        <div><small className="eyebrow">WORKSPACE OVERVIEW</small><h2 id="dashboard-heading">Dashboard</h2>
          <p>Current outreach activity at a glance.</p></div>
        <button className="secondary-button" type="button" disabled={loading} onClick={() => void loadSummary()}>
          {loading ? "Updating…" : "Refresh"}
        </button>
      </div>
      {error && <div className="dashboard-error" role="alert"><span>{error}</span>
        <button type="button" className="link-button" onClick={() => void loadSummary()}>Try again</button></div>}
      <div className="dashboard-cards">
        {loading && !summary ? <div className="dashboard-loading">Loading dashboard totals…</div> : cards.map(card => (
          <article className={`dashboard-metric ${card.tone}`} key={card.label}>
            <small>{card.label}</small><b>{card.value.toLocaleString()}</b><span>{card.hint}</span>
          </article>
        ))}
      </div>
      {summary?.recent_runs?.length ? <article className="dashboard-chart-card latest-run-card">
        <div className="dashboard-chart-heading"><div>
          <h3>Latest campaign activity</h3>
          <p>{selectedRunDetail ? `${selectedRunDetail.campaign_name} · ${new Date(selectedRunDetail.created_at).toLocaleString()}` : "Choose a recent campaign run to see its activity."}</p>
        </div><label className="run-history-select"><span>Recent campaigns</span><select aria-label="Select recent campaign activity" value={selectedRunId}
          onChange={event => setSelectedRunId(event.target.value)}>
          {summary.recent_runs.map(run => <option key={run.run_id} value={run.run_id}>
            {run.campaign_name} · {new Date(run.created_at).toLocaleString()} · {formatStatus(run.status)}
          </option>)}
        </select></label></div>
        {runDetailLoading ? <p className="chart-empty">Loading selected campaign activity…</p> : runDetailError ? <p role="alert" className="chart-empty">{runDetailError}</p> : selectedRunDetail && <>
          <div className="run-history-status"><span className={`badge ${selectedRunDetail.status}`}>{formatStatus(selectedRunDetail.status)}</span><small>Run started {new Date(selectedRunDetail.created_at).toLocaleString()}</small></div>
          <div className="latest-run-metrics">
            <div><small>Leads processed</small><b>{selectedRunDetail.processed} / {selectedRunDetail.total_leads}</b></div>
            <div><small>Waiting to send</small><b>{selectedRunDetail.queued_emails}</b></div>
            <div><small>Confirmed sent</small><b>{selectedRunDetail.sent_emails}</b></div>
            <div><small>Needs review</small><b>{selectedRunDetail.pending_review_emails}</b></div>
            {selectedRunDetail.bounced_emails > 0 && <div className="latest-run-warning"><small>Bounced</small><b>{selectedRunDetail.bounced_emails}</b></div>}
            {selectedRunDetail.cancelled_after_bounce > 0 && <div><small>Follow-ups stopped</small><b>{selectedRunDetail.cancelled_after_bounce}</b></div>}
            {selectedRunDetail.failed_emails + selectedRunDetail.failed_leads > 0 &&
              <div className="latest-run-warning"><small>Failed</small><b>{selectedRunDetail.failed_emails + selectedRunDetail.failed_leads}</b></div>}
          </div>
          <small className="latest-run-note">Counts above belong to the selected run. Workspace totals are shown in the cards above.</small>
        </>}
      </article> : null}
      {summary && <div className="dashboard-charts">
        <article className="dashboard-chart-card activity-chart-card">
          <div className="dashboard-chart-heading"><div><h3>Workspace activity</h3><p>Imported leads and received replies over time</p></div>
            <label className="chart-period-select"><span>Range</span><select aria-label="Workspace activity date range" value={activityDays} onChange={event => setActivityDays(Number(event.target.value))}>
              <option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
            </select></label></div>
          {activity?.available === false ? <p className="chart-empty">Activity charts are temporarily unavailable.</p> : <>
            <svg className="activity-chart" viewBox="0 0 720 210" role="img" aria-label="Daily imported leads and received replies over the last 14 days">
              {[0, 0.5, 1].map(fraction => {
                const y = plot.top + plot.height * fraction;
                return <g key={fraction}><line className="chart-grid" x1={plot.left} x2={plot.left + plot.width} y1={y} y2={y}/><text x="4" y={y + 3}>{Math.round(peak * (1 - fraction))}</text></g>;
              })}
              {points.length > 0 && <>
                <polyline className="chart-line-leads" points={leadSeries.map(point => `${point.x},${point.y}`).join(" ")}/>
                <polyline className="chart-line-replies" points={replySeries.map(point => `${point.x},${point.y}`).join(" ")}/>
                {leadSeries.map((point, index) => <circle className="chart-dot-leads" key={`lead-${index}`} cx={point.x} cy={point.y} r="3.5"><title>{points[index].date}: {point.value} leads</title></circle>)}
                {replySeries.map((point, index) => <circle className="chart-dot-replies" key={`reply-${index}`} cx={point.x} cy={point.y} r="3"><title>{points[index].date}: {point.value} replies</title></circle>)}
                {[0, Math.floor((points.length - 1) / 2), points.length - 1].filter((value, index, all) => all.indexOf(value) === index).map(index => <text key={index} x={leadSeries[index]?.x || plot.left} y="194" textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{new Date(`${points[index].date}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })}</text>)}
              </>}
            </svg>
            {points.every(point => point.leads === 0 && point.replies === 0) && <p className="chart-empty">No lead or reply activity in this period yet.</p>}
            <div className="chart-legend"><span><i/>Leads imported</span><span><i className="reply-legend"/>Replies received</span></div>
          </>}
        </article>
        <article className="dashboard-chart-card status-chart-card">
          <div className="dashboard-chart-heading"><div><h3>Email delivery status</h3><p>Confirmed sends and remaining work</p></div></div>
          {activity?.available === false ? <p className="chart-empty">Email status details are temporarily unavailable.</p> : emailStatusTotal === 0 ? <p className="chart-empty">Personalized email activity will appear here.</p> : <div className="email-status-chart">
            <div className="email-donut-wrap"><svg className="email-status-donut" viewBox="0 0 120 120" role="img" aria-label={`${emailStatusTotal} personalized emails by status`}>
              <circle cx="60" cy="60" r="42" stroke="#edf1ee"/>
              {ringSegments.map(item => <circle key={item.status} cx="60" cy="60" r="42" stroke={item.color}
                opacity={!selectedEmailStatus || selectedEmailStatus === item.status ? 1 : .2}
                strokeDasharray={`${item.length} ${ringLength - item.length}`} strokeDashoffset={-item.offset}>
                <title>{formatStatus(item.status)}: {item.count}</title></circle>)}
            </svg><div className="email-status-total"><b>{emailStatusTotal.toLocaleString()}</b><small>Email drafts</small></div></div>
            <div className="email-status-legend">{ringSegments.map(item => <button type="button" key={item.status}
              className={selectedEmailStatus && selectedEmailStatus !== item.status ? "muted" : ""}
              aria-pressed={selectedEmailStatus === item.status}
              onClick={() => setSelectedEmailStatus(current => current === item.status ? "" : item.status)}>
              <span><i style={{ backgroundColor: item.color }}/>{formatStatus(item.status)}</span><b>{item.count.toLocaleString()}</b></button>)}</div>
          </div>}
          {selectedEmailStatus && emailStatuses.some(item => item.status === selectedEmailStatus) && <p className="chart-selection-note">
            {formatStatus(selectedEmailStatus)}: {emailStatuses.find(item => item.status === selectedEmailStatus)?.count.toLocaleString()} emails · click again to clear
          </p>}
        </article>
      </div>}
      {summary && <article className="dashboard-chart-card bounce-activity-card">
        <div className="dashboard-chart-heading"><div><h3>Email bounce activity</h3>
          <p>Undelivered emails and addresses blocked from future campaigns</p></div></div>
        {summary.bounce_activity?.available === false ? <p className="chart-empty">Bounce details are temporarily unavailable. Refresh after the database update is applied.</p>
          : !summary.bounce_activity?.recent?.length ? <p className="chart-empty">No bounced emails have been reported.</p>
            : <div className="table"><table><thead><tr><th>Business / email</th><th>Campaign</th><th>Type</th><th>Reason</th><th>Received</th></tr></thead><tbody>
              {summary.bounce_activity.recent.map(item => <tr key={item.event_key}>
                <td>{item.business_name || "Unmatched lead"}<br/><small>{item.email || "Email not provided"}</small></td>
                <td>{item.campaign_name || "Unmatched campaign"}</td>
                <td>{item.bounce_type === "hard" ? "Hard · blocked" : item.bounce_type === "soft" ? "Temporary" : "Unknown · blocked for review"}</td>
                <td title={item.reason || "No reason supplied"}>{item.reason || "No reason supplied"}</td>
                <td>{new Date(item.event_at).toLocaleString()}</td>
              </tr>)}
            </tbody></table></div>}
      </article>}
      <div className="dashboard-links">
        <button type="button" onClick={() => onNavigate("Campaigns")}>Campaigns →</button>
        <button type="button" onClick={() => onNavigate("Human Approval")}>Human Approval →</button>
        <button type="button" onClick={() => onNavigate("Lead Replies")}>Lead Replies →</button>
      </div>
    </section>
  );
}
