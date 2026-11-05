"use client";

import { apiFetch } from "../../lib/auth";

import { useCallback, useEffect, useState } from "react";

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
  instantly_auto_launch_enabled: boolean;
  latest_run: {
    run_id: string;
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
    created_at: string;
  } | null;
  activity: {
    available?: boolean;
    days: { date: string; leads: number; replies: number }[];
    campaign_status: { status: string; count: number }[];
    email_status: { status: string; count: number }[];
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

  const cards = summary ? [
    { label: "Total leads", value: summary.total_leads, hint: "Imported and available", tone: "violet" },
    { label: "Campaigns", value: summary.total_campaigns, hint: `${summary.approved_campaigns} approved`, tone: "blue" },
    { label: "Needs approval", value: summary.pending_approvals, hint: "Human review required", tone: "amber" },
    { label: "Queued · not confirmed sent", value: summary.queued_not_sent, hint: "Waiting for Instantly status sync", tone: "amber" },
    { label: "Personalization runs", value: summary.active_runs, hint: "Currently processing", tone: "blue" },
    { label: "Instantly campaigns", value: summary.instantly_campaigns, hint: summary.instantly_auto_launch_enabled ? `${summary.instantly_active_campaigns} active · auto launch enabled` : "Draft sync only · auto launch is off", tone: "violet" },
    { label: "Leads synced to Instantly", value: summary.instantly_leads_synced, hint: "Provider lead records", tone: "green" },
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
  const formatStatus = (value: string) => value.replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase());

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
      {summary?.latest_run && <article className="dashboard-chart-card latest-run-card">
        <div className="dashboard-chart-heading"><div>
          <h3>Latest campaign run</h3>
          <p>{summary.latest_run.campaign_name} · {new Date(summary.latest_run.created_at).toLocaleString()}</p>
        </div><span className={`badge ${summary.latest_run.status}`}>{formatStatus(summary.latest_run.status)}</span></div>
        <div className="latest-run-metrics">
          <div><small>Leads processed</small><b>{summary.latest_run.processed} / {summary.latest_run.total_leads}</b></div>
          <div><small>Queued for Instantly</small><b>{summary.latest_run.queued_emails}</b></div>
          <div><small>Provider-confirmed sent</small><b>{summary.latest_run.sent_emails}</b></div>
          <div><small>Needs review</small><b>{summary.latest_run.pending_review_emails}</b></div>
          {summary.latest_run.failed_emails + summary.latest_run.failed_leads > 0 &&
            <div className="latest-run-warning"><small>Failed</small><b>{summary.latest_run.failed_emails + summary.latest_run.failed_leads}</b></div>}
        </div>
        <small className="latest-run-note">These counts are only for this run. Older campaign sends remain in the workspace totals above.</small>
      </article>}
      {summary && <div className="dashboard-charts">
        <article className="dashboard-chart-card activity-chart-card">
          <div className="dashboard-chart-heading"><div><h3>Workspace activity</h3><p>Imported leads and received replies over time</p></div>
            <label className="chart-period-select"><span>Range</span><select aria-label="Workspace activity date range" value={activityDays} onChange={event => setActivityDays(Number(event.target.value))}>
              <option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
            </select></label></div>
          {activity?.available === false ? <p className="chart-empty">Apply migration 018 to enable activity charts.</p> : <>
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
          <div className="dashboard-chart-heading"><div><h3>Email delivery status</h3><p>Provider-confirmed sends and remaining work</p></div></div>
          {activity?.available === false ? <p className="chart-empty">Apply migration 018 to enable workflow charts.</p> : emailStatusTotal === 0 ? <p className="chart-empty">Personalized email activity will appear here.</p> : <div className="email-status-chart">
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
      <div className="dashboard-links">
        <button type="button" onClick={() => onNavigate("Campaigns")}>Campaigns →</button>
        <button type="button" onClick={() => onNavigate("Human Approval")}>Human Approval →</button>
        <button type="button" onClick={() => onNavigate("Lead Replies")}>Lead Replies →</button>
      </div>
    </section>
  );
}
