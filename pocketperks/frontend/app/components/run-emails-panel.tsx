"use client";

import { useEffect, useMemo, useState } from "react";

type Lead = { id?: string; business_name?: string; email?: string; business_category?: string; city?: string; website?: string };
type PersonalizedEmail = {
  id: string; lead_id: string; step_id: string; subject: string; body: string; status: string;
  risk_level?: string; risk_reason?: string; created_at?: string;
  lead?: Lead; step?: { step_number?: number; context?: string };
};
type EmailPage = { items: PersonalizedEmail[]; total: number; has_more: boolean };
type LeadEmailDetail = { lead: Lead; items: PersonalizedEmail[] };
type Props = { apiBase: string; runId: string; processed: number; status: string };

export default function RunEmailsPanel({ apiBase, runId, processed, status }: Props) {
  const [items, setItems] = useState<PersonalizedEmail[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [moreBusy, setMoreBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedLead, setSelectedLead] = useState<LeadEmailDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [savingId, setSavingId] = useState("");
  const [detailError, setDetailError] = useState("");
  const [toast, setToast] = useState("");

  const leads = useMemo(() => {
    const grouped = new Map<string, { lead: Lead; count: number }>();
    for (const item of items) {
      const current = grouped.get(item.lead_id);
      if (current) current.count += 1;
      else grouped.set(item.lead_id, { lead: item.lead || {}, count: 1 });
    }
    return [...grouped.entries()].map(([leadId, data]) => ({ leadId, ...data }));
  }, [items]);

  useEffect(() => {
    let active = true;
    async function refreshFirstPage() {
      setBusy(items.length === 0);
      setError("");
      try {
        const response = await fetch(`${apiBase}/api/campaign-runs/${runId}/emails?limit=30&offset=0`);
        const data = await response.json() as EmailPage & { detail?: string };
        if (!response.ok) throw new Error(data.detail || "Could not load personalized emails.");
        if (!active) return;
        setItems(current => {
          const byId = new Map<string, PersonalizedEmail>();
          for (const item of [...current, ...data.items]) byId.set(item.id, item);
          return [...byId.values()].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
        });
        setTotal(data.total);
        setHasMore(data.has_more);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load personalized emails.");
      } finally {
        if (active) setBusy(false);
      }
    }
    void refreshFirstPage();
    return () => { active = false; };
  // Refresh as chunk progress advances; this keeps new generated emails visible during the run.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, runId, processed, status]);

  async function loadMore() {
    if (moreBusy || !hasMore) return;
    setMoreBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/campaign-runs/${runId}/emails?limit=30&offset=${items.length}`);
      const data = await response.json() as EmailPage & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load more personalized emails.");
      setItems(current => {
        const byId = new Map(current.map(item => [item.id, item]));
        for (const item of data.items) byId.set(item.id, item);
        return [...byId.values()];
      });
      setTotal(data.total);
      setHasMore(data.has_more);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load more personalized emails.");
    } finally {
      setMoreBusy(false);
    }
  }

  async function openLead(leadId: string) {
    setDetailBusy(true);
    setDetailError("");
    setSelectedLead(null);
    try {
      const response = await fetch(`${apiBase}/api/campaign-runs/${runId}/leads/${leadId}/emails`);
      const data = await response.json() as LeadEmailDetail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load this lead's personalized emails.");
      setSelectedLead(data);
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : "Could not load this lead's personalized emails.");
    } finally {
      setDetailBusy(false);
    }
  }

  function changeEmail(emailId: string, field: "subject" | "body", value: string) {
    setSelectedLead(current => current ? ({ ...current, items: current.items.map(item =>
      item.id === emailId ? { ...item, [field]: value } : item) }) : current);
  }

  async function saveEmail(email: PersonalizedEmail) {
    setSavingId(email.id);
    setDetailError("");
    setToast("");
    try {
      const response = await fetch(`${apiBase}/api/emails/${email.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: email.subject, body: email.body }),
      });
      const data = await response.json() as PersonalizedEmail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not save this email.");
      setSelectedLead(current => current ? ({ ...current, items: current.items.map(item => item.id === email.id ? { ...item, ...data } : item) }) : current);
      setItems(current => current.map(item => item.id === email.id ? { ...item, ...data } : item));
      setToast("Email saved. It is in Human Approval for review.");
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : "Could not save this email.");
    } finally {
      setSavingId("");
    }
  }

  return (
    <>
      <section className="run-emails card" aria-label="Personalized emails">
        <div className="run-emails-heading">
          <div><h3>Personalized emails <span>{total}</span></h3><p>Generated email drafts · not sent</p></div>
          {status === "running" && <small>Updating as leads finish…</small>}
        </div>
        {error && <p role="alert" className="error">{error}</p>}
        {busy && items.length === 0 ? <p>Loading personalized emails…</p> : leads.length === 0 ? (
          <p className="run-emails-empty">{status === "running" ? "Personalized emails will appear here as they are generated." : "No personalized emails were generated for this run."}</p>
        ) : (
          <>
            <div className="personalized-lead-list" role="list" aria-label="Personalized emails by lead">
              {leads.map(({ leadId, lead, count }) => (
                <article className="personalized-lead-row" key={leadId}>
                  <div><b>{lead.business_name || lead.email || "Business"}</b><small>{lead.email || "Lead email unavailable"} · {count} email{count === 1 ? "" : "s"} loaded</small></div>
                  <button type="button" className="secondary-button" onClick={() => void openLead(leadId)}>View Personalized Email →</button>
                </article>
              ))}
            </div>
            {hasMore && <button type="button" className="secondary-button run-email-more" disabled={moreBusy} onClick={() => void loadMore()}>
              {moreBusy ? "Loading…" : `Load more leads (${leads.length} shown · ${total} emails)`}
            </button>}
          </>
        )}
      </section>

      {(selectedLead || detailBusy || detailError) && <div className="personalized-email-page" role="dialog" aria-modal="true" aria-label="Lead personalized emails">
        <header className="personalized-email-topbar"><button type="button" className="secondary-button" onClick={() => { setSelectedLead(null); setDetailError(""); }}>← Back to leads</button><span>PocketPerks · Personalized email review</span></header>
        <main className="personalized-email-content">
          {detailBusy ? <div className="card">Loading this lead’s emails…</div> : detailError && !selectedLead ? <p role="alert" className="error">{detailError}</p> : selectedLead && <>
            <small className="eyebrow">LEAD EMAILS</small>
            <div className="personalized-lead-title"><div><h1>{selectedLead.lead.business_name || "Personalized emails"}</h1><p>{[selectedLead.lead.business_category, selectedLead.lead.city, selectedLead.lead.email].filter(Boolean).join(" · ")}</p></div><span className="badge generated">{selectedLead.items.length} email{selectedLead.items.length === 1 ? "" : "s"}</span></div>
            <p className="personalized-notice">These are personalized drafts and have not been sent. Saving an edit sends the email to Human Approval for review.</p>
            {detailError && <p role="alert" className="error">{detailError}</p>}
            {toast && <p role="status" className="personalized-saved">✓ {toast}</p>}
            {selectedLead.items.map(email => <article className="card personalized-email-card" key={email.id}>
              <div className="personalized-email-card-head"><div><small className="eyebrow">EMAIL {email.step?.step_number ?? "—"}</small><h2>Email {email.step?.step_number ?? ""}</h2></div><span className={`badge ${email.status}`}>{email.status.replaceAll("_", " ")}</span></div>
              {email.step?.context && <p className="personalized-email-context">{email.step.context}</p>}
              <label className="personalized-field">Subject<input value={email.subject} onChange={event => changeEmail(email.id, "subject", event.target.value)} maxLength={500} /></label>
              <label className="personalized-field">Body<textarea rows={10} value={email.body} onChange={event => changeEmail(email.id, "body", event.target.value)} maxLength={20000} /></label>
              {email.risk_reason && <small className="personalized-risk">Current risk: {email.risk_level || "unknown"} · {email.risk_reason}</small>}
              <div className="personalized-save-row"><button type="button" className="action-button" disabled={!!savingId} onClick={() => void saveEmail(email)}>{savingId === email.id ? "Saving…" : "Save changes"}</button></div>
            </article>)}
          </>}
        </main>
      </div>}
    </>
  );
}
