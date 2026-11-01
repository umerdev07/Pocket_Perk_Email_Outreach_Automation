"use client";

import { apiFetch } from "../../lib/auth";

import { useEffect, useState } from "react";

type ApprovalEmail = {
  id: string;
  subject: string;
  body: string;
  status: "pending_review";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  risk_reason: string;
  lead: { business_name?: string; email?: string; business_category?: string; city?: string };
  campaign: { name?: string; instructions?: string };
  step: { step_number?: number; context?: string };
};

type QueuePage = { items: ApprovalEmail[]; total: number; has_more: boolean };
type HistoryEntry = {
  id: string; action: string; status: string; risk_level: "LOW" | "MEDIUM" | "HIGH" | null;
  risk_reason: string | null; subject: string; business_name: string | null;
  recipient_email: string | null; campaign_name: string | null; step_number: number | null;
  created_at: string;
};
type Props = { apiBase: string; notify: (message: string) => void };

export default function ApprovalQueue({ apiBase, notify }: Props) {
  const [items, setItems] = useState<ApprovalEmail[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [moreBusy, setMoreBusy] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"review" | "history">("review");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  async function loadQueue(offset = 0, append = false) {
    setLoading(!append);
    setMoreBusy(append);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/approvals?limit=50&offset=${offset}`);
      const data = await response.json() as QueuePage & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load the approval queue.");
      setItems(current => append ? [...current, ...data.items] : data.items);
      setTotal(data.total);
      setHasMore(data.has_more);
      if (!append) setSelectedId(data.items[0]?.id || "");
      else if (!selectedId && data.items[0]) setSelectedId(data.items[0].id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the approval queue.");
    } finally {
      setLoading(false);
      setMoreBusy(false);
    }
  }

  useEffect(() => { void loadQueue(); /* Load once when the page opens. */ }, []);

  async function loadHistory() {
    setHistoryLoading(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/approvals/history?limit=100&offset=0`);
      const data = await response.json() as HistoryEntry[] & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load email review history.");
      setHistory(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load email review history.");
    } finally {
      setHistoryLoading(false);
    }
  }

  const selected = items.find(item => item.id === selectedId);
  useEffect(() => {
    setSubject(selected?.subject || "");
    setBody(selected?.body || "");
  }, [selected?.id]);

  async function saveEdit() {
    if (!selected || busy) return;
    setBusy(selected.id);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/emails/${selected.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, body }),
      });
      const data = await response.json() as { detail?: string; status?: string };
      if (!response.ok) throw new Error(data.detail || "Could not save this email edit.");
      await loadQueue();
      notify(data.status === "queued" ? "Email saved and queued after the spam-risk check. It has not been sent."
        : "Email saved. A spam-risk signal still needs human review.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save this email edit.");
    } finally {
      setBusy("");
    }
  }

  async function recheckRisk() {
    if (!selected || busy) return;
    setBusy(selected.id);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/emails/${selected.id}/recheck`, { method: "POST" });
      const data = await response.json() as { auto_queued?: boolean; risk_level?: "LOW" | "MEDIUM" | "HIGH";
        risk_reason?: string; detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not recheck this email.");
      if (data.auto_queued) {
        const remaining = items.filter(item => item.id !== selected.id);
        setItems(remaining);
        setTotal(count => Math.max(0, count - 1));
        setSelectedId(remaining[0]?.id || "");
        notify("Risk rechecked: low risk and queued. It has not been sent.");
      } else {
        setItems(current => current.map(item => item.id === selected.id ? {
          ...item, risk_level: data.risk_level || item.risk_level,
          risk_reason: data.risk_reason || item.risk_reason,
        } : item));
        notify("Risk rechecked. This email still needs human review.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not recheck this email.");
    } finally {
      setBusy("");
    }
  }

  async function decide(action: "approve" | "reject") {
    if (!selected || busy) return;
    setBusy(selected.id);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/emails/${selected.id}/${action}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Could not ${action} this email.`);
      const remaining = items.filter(item => item.id !== selected.id);
      setItems(remaining);
      setTotal(count => Math.max(0, count - 1));
      setSelectedId(remaining[0]?.id || "");
      notify(action === "approve" ? "Email approved and queued. It has not been sent." : "Email rejected");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not ${action} this email.`);
    } finally {
      setBusy("");
    }
  }

  const mediumCount = items.filter(item => item.risk_level === "MEDIUM").length;
  const highCount = items.filter(item => item.risk_level === "HIGH").length;

  return (
    <div className="content approval-page">
      <small className="eyebrow">REVIEW REQUIRED</small>
      <div className="title approval-title">
        <div><h1>Human Approval</h1><p>Review emails whose wording or formatting may look spam-like. This is a content risk estimate; Gmail also checks sender and domain signals.</p></div>
        <button className="secondary-button" type="button" disabled={loading} onClick={() => void loadQueue()}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <div className="approval-summary">
        <article><small>Waiting for review</small><b>{total}</b></article>
        <article><small>High risk in this page</small><b className="risk-high-text">{highCount}</b></article>
        <article><small>Medium risk in this page</small><b className="risk-medium-text">{mediumCount}</b></article>
      </div>
      <div className="approval-tabs" role="tablist" aria-label="Email review">
        <button type="button" role="tab" aria-selected={tab === "review"} className={tab === "review" ? "active" : ""}
          onClick={() => setTab("review")}>Needs review <span>{total}</span></button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "active" : ""}
          onClick={() => { setTab("history"); if (!history.length) void loadHistory(); }}>Review history</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {tab === "history" ? historyLoading ? <section className="card">Loading review history…</section> : history.length === 0 ? (
        <section className="card approval-empty"><span>↻</span><h2>No review history yet</h2>
          <p>Automated spam-risk checks and human review decisions will appear here.</p></section>
      ) : <section className="card review-history-card">
        <div className="approval-list-heading"><h2>Recent email checks and decisions</h2>
          <button className="secondary-button" type="button" disabled={historyLoading} onClick={() => void loadHistory()}>{historyLoading ? "Refreshing…" : "Refresh history"}</button></div>
        <div className="review-history-list">{history.map(entry => <article className="review-history-item" key={entry.id}>
          <div className="review-history-top"><div><b>{entry.business_name || entry.recipient_email || "Email"}</b>
            <small>{entry.campaign_name || "Campaign"} · Email {entry.step_number ?? "—"} · {new Date(entry.created_at).toLocaleString()}</small></div>
            <span className={`risk-badge ${(entry.risk_level || "low").toLowerCase()}`}>{entry.risk_level || "—"} · {entry.status.replaceAll("_", " ")}</span></div>
          <p className="review-history-subject">{entry.subject}</p>
          <small className="review-history-action">{entry.action.replaceAll("_", " ")}</small>
          {entry.risk_reason && <p className="review-history-reason">{entry.risk_reason}</p>}
        </article>)}</div>
      </section> : loading && items.length === 0 ? <section className="card">Loading approval queue…</section> : total === 0 ? (
        <section className="card approval-empty"><span>✓</span><h2>No emails need review</h2>
          <p>Emails that need human review will appear here.</p></section>
      ) : (
        <div className="approval-workspace">
          <section className="card approval-list-card">
            <div className="approval-list-heading"><h2>Emails to review</h2><span>{total}</span></div>
            <div className="approval-list">
              {items.map(item => <button type="button" key={item.id}
                className={`approval-item${selectedId === item.id ? " selected" : ""}`}
                onClick={() => setSelectedId(item.id)}>
                <span className={`risk-badge ${item.risk_level.toLowerCase()}`}>{item.risk_level} risk</span>
                <b>{item.lead.business_name || item.lead.email || "Unknown business"}</b>
                <small>{item.campaign.name || "Campaign"} · Email {item.step.step_number ?? "—"}</small>
                <span className="approval-subject">{item.subject}</span>
              </button>)}
            </div>
            {hasMore && <button type="button" className="secondary-button approval-more" disabled={loading || moreBusy}
              onClick={() => void loadQueue(items.length, true)}>{moreBusy ? "Loading…" : "Load more"}</button>}
          </section>
          {selected && <section className="card approval-detail">
            <div className="approval-detail-head">
              <div><small className="eyebrow">{selected.campaign.name || "Campaign"}</small>
                <h2>{selected.lead.business_name || "Business"}</h2>
                <p>{selected.lead.email} · {selected.lead.city || "Location unavailable"} · Email {selected.step.step_number ?? "—"}</p>
              </div>
              <span className={`risk-badge ${selected.risk_level.toLowerCase()}`}>{selected.risk_level} risk</span>
            </div>
            <div className="risk-reason"><b>Why this email may look like spam</b><p>{selected.risk_reason || "The content check flagged this email for review."}</p></div>
            <p className="approval-context"><b>Campaign instructions:</b> {selected.campaign.instructions || "No campaign instructions recorded."}</p>
            {selected.step.context && <p className="approval-context"><b>Email context:</b> {selected.step.context}</p>}
            <p className="approval-send-note">Approving places this email in the send queue. It will not be marked sent until Instantly confirms a real send.</p>
            <label className="approval-field">To<input type="email" value={selected.lead.email || "Recipient email unavailable"} readOnly /></label>
            <label className="approval-field">Subject<input value={subject} onChange={event => setSubject(event.target.value)} /></label>
            <label className="approval-field">Email body<textarea rows={10} value={body} onChange={event => setBody(event.target.value)} /></label>
            <div className="approval-actions">
              <button type="button" className="secondary-button" disabled={!!busy}
                onClick={() => void recheckRisk()}>{busy === selected.id ? "Checking…" : "Recheck risk"}</button>
              <button type="button" className="secondary-button" disabled={busy === selected.id || !subject.trim() || !body.trim()}
                onClick={() => void saveEdit()}>{busy === selected.id ? "Saving…" : "Save edit"}</button>
              <button type="button" className="reject-button" disabled={!!busy} onClick={() => void decide("reject")}>
                {busy === selected.id ? "Working…" : "Reject"}</button>
              <button type="button" className="action-button" disabled={!!busy} onClick={() => void decide("approve")}>
                {busy === selected.id ? "Working…" : "Approve"}</button>
            </div>
          </section>}
        </div>
      )}
    </div>
  );
}
