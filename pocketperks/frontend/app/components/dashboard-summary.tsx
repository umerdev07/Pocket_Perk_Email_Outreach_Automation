"use client";

import { useCallback, useEffect, useState } from "react";

type Summary = {
  total_leads: number;
  total_campaigns: number;
  approved_campaigns: number;
  pending_approvals: number;
  queued_not_sent: number;
  active_runs: number;
};

type Props = {
  apiBase: string;
  refreshKey: number;
  onNavigate: (page: "Campaigns" | "Human Approval") => void;
};

export default function DashboardSummary({ apiBase, refreshKey, onNavigate }: Props) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/dashboard/summary`);
      const data = await response.json() as Summary & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load dashboard totals.");
      setSummary(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load dashboard totals.");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { void loadSummary(); }, [loadSummary, refreshKey]);

  const cards = summary ? [
    { label: "Total leads", value: summary.total_leads, hint: "Imported and available", tone: "violet" },
    { label: "Campaigns", value: summary.total_campaigns, hint: `${summary.approved_campaigns} approved`, tone: "blue" },
    { label: "Needs approval", value: summary.pending_approvals, hint: "Human review required", tone: "amber" },
    { label: "Queued · not sent", value: summary.queued_not_sent, hint: "Ready for sending setup", tone: "green" },
    { label: "Personalization runs", value: summary.active_runs, hint: "Currently processing", tone: "blue" },
  ] : [];

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
      <div className="dashboard-links">
        <button type="button" onClick={() => onNavigate("Campaigns")}>Campaigns →</button>
        <button type="button" onClick={() => onNavigate("Human Approval")}>Human Approval →</button>
      </div>
    </section>
  );
}
