"use client";

import { apiFetch } from "../../lib/auth";
import { userFacingError } from "../../lib/api";

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
  step: { step_number?: number; context?: string; trigger_type?: "scheduled"|"after_conversation"|"close_loop" };
};

type QueuePage = { items: ApprovalEmail[]; total: number; has_more: boolean };
type HistoryEntry = {
  id: string; action: string; status: string; risk_level: "LOW" | "MEDIUM" | "HIGH" | null;
  risk_reason: string | null; subject: string; business_name: string | null;
  recipient_email: string | null; campaign_name: string | null; step_number: number | null;
  created_at: string;
};
type HistoryPage = { items: HistoryEntry[]; total: number; has_more: boolean; limit: number; offset: number };
type Props = { apiBase: string; notify: (message: string) => void; canClearHistory: boolean };

export default function ApprovalQueue({ apiBase, notify, canClearHistory }: Props) {
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
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [historyDays, setHistoryDays] = useState("30");

  async function loadQueue(offset = 0, append = false) {
    setLoading(!append);
    setMoreBusy(append);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/approvals?limit=50&offset=${offset}`);
      const data = await response.json() as QueuePage & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load emails for review.");
      setItems(current => append ? [...current, ...data.items] : data.items);
      setTotal(data.total);
      setHasMore(data.has_more);
      if (!append) setSelectedId(data.items[0]?.id || "");
      else if (!selectedId && data.items[0]) setSelectedId(data.items[0].id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load emails for review.");
    } finally {
      setLoading(false);
      setMoreBusy(false);
    }
  }

  useEffect(() => { void loadQueue(); /* Load once when the page opens. */ }, []);

  async function loadHistory(offset = 0, append = false, overrides?: { search: string; days: string }) {
    setHistoryLoading(true);
    setError("");
    try {
      const filters = overrides || { search: historySearch.trim(), days: historyDays };
      const params = new URLSearchParams({ limit: "50", offset: String(offset), days: filters.days });
      if (filters.search.trim()) params.set("search", filters.search.trim());
      const response = await apiFetch(`${apiBase}/api/approvals/history?${params}`);
      const data = await response.json() as HistoryPage & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load email review history.");
      setHistory(current => append ? [...current, ...data.items] : data.items);
      setHistoryTotal(data.total);
      setHistoryHasMore(data.has_more);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load email review history.");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function clearHistory() {
    const confirmed = window.confirm("Clear all Human Approval history? This permanently removes the review-history log only. Emails, pending reviews, leads and campaigns will stay unchanged.");
    if (!confirmed) return;
    setHistoryLoading(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/approvals/history/clear`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }),
      });
      const data = await response.json() as { detail?: string; cleared?: boolean };
      if (!response.ok) throw new Error(data.detail || "Could not clear review history.");
      setHistory([]);
      setHistoryTotal(0);
      setHistoryHasMore(false);
      setHistorySearch("");
      setHistoryDays("30");
      notify("Human Approval history cleared. Email and campaign data were kept.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not clear review history.");
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
      notify(data.status === "queued" ? "Email saved and ready to send. It has not been sent yet."
        : data.status === "approved" ? "Email saved. You can send it after confirming a visit or conversation."
        : "Email saved. Please review the message before it can be sent.");
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
      const data = await response.json() as { auto_queued?: boolean; ready_for_manual_trigger?: boolean; risk_level?: "LOW" | "MEDIUM" | "HIGH";
        risk_reason?: string; detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not recheck this email.");
      if (data.auto_queued || data.ready_for_manual_trigger) {
        const remaining = items.filter(item => item.id !== selected.id);
        setItems(remaining);
        setTotal(count => Math.max(0, count - 1));
        setSelectedId(remaining[0]?.id || "");
        notify(data.ready_for_manual_trigger ? "Message checked. You can send it after confirming a visit or conversation."
          : "Message checked and ready to send. It has not been sent yet.");
      } else {
        setItems(current => current.map(item => item.id === selected.id ? {
          ...item, risk_level: data.risk_level || item.risk_level,
          risk_reason: data.risk_reason || item.risk_reason,
        } : item));
        notify("Message checked. It still needs your review.");
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
      notify(action === "approve" ? (selected.step.trigger_type === "after_conversation"
        ? "Email approved. You can send it after confirming a visit or conversation." : "Email approved and ready to send. It has not been sent yet.") : "Email rejected");
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
        <div><h1>Human Approval</h1><p>Review messages that may need a wording or formatting change before sending. This check looks at the message; inbox placement also depends on the sender and their email setup.</p></div>
        <button className="secondary-button" type="button" disabled={loading} onClick={() => void loadQueue()}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <div className="approval-summary">
        <article><small>Waiting for review</small><b>{total}</b></article>
        <article><small>Needs careful review</small><b className="risk-high-text">{highCount}</b></article>
        <article><small>Review suggested</small><b className="risk-medium-text">{mediumCount}</b></article>
      </div>
      <div className="approval-tabs" role="tablist" aria-label="Email review">
        <button type="button" role="tab" aria-selected={tab === "review"} className={tab === "review" ? "active" : ""}
          onClick={() => setTab("review")}>Needs review <span>{total}</span></button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "active" : ""}
          onClick={() => { setTab("history"); if (!history.length) void loadHistory(); }}>Review history</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {tab === "history" ? <section className="card review-history-card">
        <div className="approval-list-heading"><h2>Recent email checks and decisions</h2>
          <span>{historyTotal.toLocaleString()} matching records</span>
          <div className="review-history-actions"><button className="secondary-button" type="button" disabled={historyLoading} onClick={() => void loadHistory()}>{historyLoading ? "Updating…" : "Apply filters"}</button>
            {canClearHistory && <button className="reject-button" type="button" disabled={historyLoading} onClick={() => void clearHistory()}>Clear history</button>}</div></div>
        <div className="review-history-filters">
          <label>Search<input value={historySearch} placeholder="Business, email, subject…" onChange={event => setHistorySearch(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void loadHistory(); }} /></label>
          <label>Date range<select value={historyDays} onChange={event => setHistoryDays(event.target.value)}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="365">Last year</option><option value="0">All time</option></select></label>
          <button className="link-button" type="button" onClick={() => { setHistorySearch(""); setHistoryDays("30");
            void loadHistory(0, false, { search: "", days: "30" }); }}>Reset filters</button>
        </div>
        {historyLoading && history.length === 0 ? <p className="chart-empty">Loading review history…</p> : history.length === 0 ? <div className="approval-empty"><span>↻</span><h2>No matching history</h2><p>Try widening the date range or clearing a filter.</p></div> : <div className="review-history-list">{history.map(entry => <article className="review-history-item" key={entry.id}>
          <div className="review-history-top"><div><b>{entry.business_name || entry.recipient_email || "Email"}</b>
            <small>{entry.campaign_name || "Campaign"} · Email {entry.step_number ?? "—"} · {new Date(entry.created_at).toLocaleString()}</small></div>
            <span className={`risk-badge ${(entry.risk_level || "low").toLowerCase()}`}>{entry.risk_level === "HIGH" ? "Careful review" : entry.risk_level === "MEDIUM" ? "Review suggested" : "Checked"} · {entry.status === "auto_queued" || entry.status === "queued" ? "Ready to send" : entry.status.replaceAll("_", " ")}</span></div>
          <p className="review-history-subject">{entry.subject}</p>
          <small className="review-history-action">{entry.action.replaceAll("_", " ")}</small>
          {entry.risk_reason && <p className="review-history-reason">{userFacingError(entry.risk_reason.replace(/^AI\s+(?:spam[- ]risk|content)\s+check:\s*/i, "Message check: "), "The message was flagged for a quick review.")}</p>}
        </article>)}</div>}
        {historyHasMore && <button className="secondary-button approval-more" type="button" disabled={historyLoading}
          onClick={() => void loadHistory(history.length, true)}>{historyLoading ? "Loading…" : `Load more (${history.length} of ${historyTotal})`}</button>}
      </section> : loading && items.length === 0 ? <section className="card">Loading emails for review…</section> : total === 0 ? (
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
                <span className={`risk-badge ${item.risk_level.toLowerCase()}`}>{item.risk_level === "HIGH" ? "Careful review" : "Review suggested"}</span>
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
              <span className={`risk-badge ${selected.risk_level.toLowerCase()}`}>{selected.risk_level === "HIGH" ? "Careful review" : "Review suggested"}</span>
            </div>
            <div className="risk-reason"><b>Why this message needs a review</b><p>{userFacingError(selected.risk_reason.replace(/^AI\s+(?:spam[- ]risk|content)\s+check:\s*/i, "Message check: "), "The message was flagged for a quick review.")}</p></div>
            <p className="approval-context"><b>Campaign instructions:</b> {selected.campaign.instructions || "No campaign instructions recorded."}</p>
            {selected.step.context && <p className="approval-context"><b>Email context:</b> {selected.step.context}</p>}
            <p className="approval-send-note">{selected.step.trigger_type === "after_conversation"
              ? "Approval prepares this email, but it will only send after you confirm a real visit or conversation in the email details."
              : "Approving makes this email ready to send. It will show as sent after delivery is confirmed."}</p>
            <label className="approval-field">To<input type="email" value={selected.lead.email || "Recipient email unavailable"} readOnly /></label>
            <label className="approval-field">Subject<input value={subject} onChange={event => setSubject(event.target.value)} /></label>
            <label className="approval-field">Email body<textarea rows={10} value={body} onChange={event => setBody(event.target.value)} /></label>
            <div className="approval-actions">
              <button type="button" className="secondary-button" disabled={!!busy}
                onClick={() => void recheckRisk()}>{busy === selected.id ? "Checking…" : "Check message again"}</button>
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
