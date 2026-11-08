"use client";

import { apiFetch } from "../../lib/auth";

import { useEffect, useRef, useState } from "react";

type Batch = { id: string; file_name: string; total_rows?: number; imported_count: number; duplicate_count?: number;
  invalid_count?: number; rejected_count?: number; suppressed_count?: number; created_at?: string };
type Lead = Record<string, string | null> & { id: string; business_name?: string; email?: string };
type Rejected = { row: number; status: string; data?: Record<string, string> };
type Email = { id: string; subject: string; body: string; status: string; risk_level?: string; risk_reason?: string;
  step?: { step_number?: number; context?: string }; campaign?: { name?: string } };
type LeadEmails = { lead: Lead; items: Email[] };
type Page = { batch: Batch; view: "imported" | "not_imported"; rows: Lead[] | Rejected[]; total: number;
  limit: number; offset: number; has_more: boolean };

const leadColumns: [string, string][] = [
  ["business_name", "Business name"], ["first_name", "First name"], ["business_category", "Category"], ["email", "Email"],
  ["phone", "Phone"], ["website", "Website"], ["domain", "Domain"], ["city", "City"],
  ["full_address", "Full address"], ["email_source_url", "Email source"],
  ["verification_result", "Verification"], ["verification_tool", "Verification tool"], ["quality_flags", "Quality flags"],
];

export default function BatchDetailsPanel({ apiBase, batch, onStart }: { apiBase: string; batch: Batch; onStart: () => void }) {
  const [view, setView] = useState<"imported" | "not_imported">("imported");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [expandedLead, setExpandedLead] = useState("");
  const [emailsByLead, setEmailsByLead] = useState<Record<string, LeadEmails>>({});
  const [emailBusy, setEmailBusy] = useState("");
  const [savingId, setSavingId] = useState("");
  const [emailError, setEmailError] = useState("");
  const [savedMessage, setSavedMessage] = useState("");
  const recordsScroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setBusy(true); setError("");
      try {
        const response = await apiFetch(`${apiBase}/api/lead-batches/${batch.id}/details?view=${view}&limit=50&offset=${offset}`);
        const data = await response.json() as Page & { detail?: string };
        if (!response.ok) throw new Error(data.detail || "Could not load this import’s records.");
        if (active) setPage(data);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load this import’s records.");
      } finally { if (active) setBusy(false); }
    }
    void load();
    return () => { active = false; };
  }, [apiBase, batch.id, view, offset]);

  function changeView(next: "imported" | "not_imported") { setView(next); setOffset(0); setPage(null); setExpandedLead(""); }

  function scrollRecords(direction: -1 | 1) {
    recordsScroller.current?.scrollBy({ left: direction * Math.max(260, recordsScroller.current.clientWidth * 0.75), behavior: "smooth" });
  }

  async function toggleLead(leadId: string) {
    if (expandedLead === leadId) { setExpandedLead(""); return; }
    setExpandedLead(leadId); setEmailError(""); setSavedMessage("");
    if (emailsByLead[leadId]) return;
    setEmailBusy(leadId);
    try {
      const response = await apiFetch(`${apiBase}/api/lead-batches/${batch.id}/leads/${leadId}/personalized-emails`);
      const data = await response.json() as LeadEmails & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not load personalized emails for this lead.");
      setEmailsByLead(current => ({ ...current, [leadId]: data }));
    } catch (cause) {
      setEmailError(cause instanceof Error ? cause.message : "Could not load personalized emails for this lead.");
    } finally { setEmailBusy(""); }
  }

  function changeEmail(leadId: string, emailId: string, field: "subject" | "body", value: string) {
    setEmailsByLead(current => {
      const detail = current[leadId];
      if (!detail) return current;
      return { ...current, [leadId]: { ...detail, items: detail.items.map(item => item.id === emailId ? { ...item, [field]: value } : item) } };
    });
  }

  async function saveEmail(leadId: string, email: Email) {
    setSavingId(email.id); setEmailError(""); setSavedMessage("");
    try {
      const response = await apiFetch(`${apiBase}/api/emails/${email.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: email.subject, body: email.body }),
      });
      const data = await response.json() as Email & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Could not save this email.");
      setEmailsByLead(current => {
        const detail = current[leadId];
        return detail ? { ...current, [leadId]: { ...detail, items: detail.items.map(item => item.id === email.id ? { ...item, ...data } : item) } } : current;
      });
      setSavedMessage("Email saved. It is now in Human Approval for review; it has not been sent.");
    } catch (cause) {
      setEmailError(cause instanceof Error ? cause.message : "Could not save this email.");
    } finally { setSavingId(""); }
  }

  const pageRows = page?.rows || [];
  const first = page && page.total ? page.offset + 1 : 0;
  const last = page ? Math.min(page.offset + page.rows.length, page.total) : 0;

  return <section className="batch-details" aria-label={`Import details for ${batch.file_name}`}>
    {(() => { const detailBatch = page?.batch || batch; const rejectedCount = (detailBatch.duplicate_count || 0) + (detailBatch.invalid_count || 0) + (detailBatch.rejected_count || 0); return <>
    <div className="batch-details-heading"><div><small className="eyebrow">IMPORT DETAILS</small><h3>{detailBatch.file_name}</h3><p>{detailBatch.total_rows ?? detailBatch.imported_count} source rows · {detailBatch.imported_count} imported</p></div><button className="action-button" disabled={!detailBatch.imported_count} onClick={onStart}>Start Campaign →</button></div>
    <div className="batch-detail-stats"><span>Imported <b>{detailBatch.imported_count}</b></span><span>Duplicates <b>{detailBatch.duplicate_count ?? 0}</b></span><span>Invalid <b>{detailBatch.invalid_count ?? 0}</b></span><span>Rejected <b>{detailBatch.rejected_count ?? 0}</b></span><span>Opted out <b>{detailBatch.suppressed_count ?? 0}</b></span></div>
    <div className="batch-detail-tabs"><button className={view === "imported" ? "active" : ""} onClick={() => changeView("imported")}>Imported leads ({detailBatch.imported_count})</button><button className={view === "not_imported" ? "active" : ""} onClick={() => changeView("not_imported")}>Not imported ({rejectedCount})</button></div>
    </>; })()}
    {error && <p className="error" role="alert">{error}</p>}
    {busy ? <p className="batch-detail-loading">Loading records…</p> : pageRows.length === 0 ? <p className="batch-detail-loading">No {view === "imported" ? "imported leads" : "rejected rows"} in this section.</p> : view === "imported" ? <>
      <p className="batch-page-count">Showing {first}–{last} of {page?.total.toLocaleString()} imported leads</p>
      <div className="batch-table-controls"><span>Scroll to see all lead columns</span><div><button type="button" aria-label="Scroll table left" onClick={() => scrollRecords(-1)}>←</button><button type="button" aria-label="Scroll table right" onClick={() => scrollRecords(1)}>→</button></div></div>
      <div className="table batch-record-table" ref={recordsScroller} tabIndex={0} aria-label="Imported lead records; horizontally scroll to see additional columns"><table><thead><tr>{leadColumns.map(([, label]) => <th key={label}>{label}</th>)}<th>Personalized emails</th></tr></thead><tbody>
        {(pageRows as Lead[]).map(lead => <tr key={lead.id}>{leadColumns.map(([key]) => <td key={key}>{lead[key] || "—"}</td>)}<td><button className="batch-view-email" aria-expanded={expandedLead === lead.id} onClick={() => void toggleLead(lead.id)}>{expandedLead === lead.id ? "Hide emails" : "View Personalized Email"}</button></td></tr>)}
      </tbody></table></div>
      {expandedLead && <section className="batch-lead-emails" aria-label="Personalized emails for selected lead">
        <div className="batch-lead-emails-heading"><div><small className="eyebrow">PERSONALIZED EMAILS</small><h3>{emailsByLead[expandedLead]?.lead.business_name || (pageRows as Lead[]).find(lead => lead.id === expandedLead)?.business_name || "Selected lead"}</h3><p>{emailsByLead[expandedLead]?.lead.email || (pageRows as Lead[]).find(lead => lead.id === expandedLead)?.email || ""}</p></div><button className="secondary-button" onClick={() => setExpandedLead("")}>Close</button></div>
        <p className="batch-emails-note">Review each email below. These drafts have not been sent; saving edits sends them to Human Approval.</p>
        {emailBusy === expandedLead ? <p className="batch-detail-loading">Loading this lead’s personalized emails…</p> : emailError ? <p role="alert" className="error">{emailError}</p> : !(emailsByLead[expandedLead]?.items.length) ? <p className="batch-detail-loading">No personalized emails found for this lead yet.</p> : <div className="batch-email-cards">{emailsByLead[expandedLead].items.map(email => <article className="batch-email-card" key={email.id}>
          <div className="batch-email-card-head"><div><small className="eyebrow">{email.campaign?.name || "Campaign"}</small><h4>Email {email.step?.step_number ?? "—"}</h4></div><span className={`badge ${email.status}`}>{email.status.replaceAll("_", " ")}</span></div>
          {email.step?.context && <p className="batch-email-context"><b>Context</b><br/>{email.step.context}</p>}
          <label>To<input type="email" value={emailsByLead[expandedLead].lead.email || "Recipient email unavailable"} readOnly/></label>
          <label>Subject<input value={email.subject} onChange={event => changeEmail(expandedLead, email.id, "subject", event.target.value)}/></label>
          <label>Body<textarea rows={8} value={email.body} onChange={event => changeEmail(expandedLead, email.id, "body", event.target.value)}/></label>
          {email.risk_reason && <small className="batch-email-risk">Risk: {email.risk_level || "unknown"} · {email.risk_reason}</small>}
          <div className="batch-email-save"><button className="action-button" disabled={!!savingId} onClick={() => void saveEmail(expandedLead, email)}>{savingId === email.id ? "Saving…" : "Save changes"}</button></div>
        </article>)}</div>}
        {savedMessage && <p className="batch-saved-message" role="status">✓ {savedMessage}</p>}
      </section>}
    </> : <>
      <p className="batch-page-count">Showing {first}–{last} of {page?.total.toLocaleString()} rows not imported</p>
      <div className="batch-table-controls"><span>Scroll to see all source columns</span><div><button type="button" aria-label="Scroll table left" onClick={() => scrollRecords(-1)}>←</button><button type="button" aria-label="Scroll table right" onClick={() => scrollRecords(1)}>→</button></div></div>
      <div className="table batch-record-table" ref={recordsScroller} tabIndex={0} aria-label="Not imported source rows; horizontally scroll to see additional columns"><table><thead><tr><th>Source row</th><th>Result</th><th>Business name</th><th>First name</th><th>Category</th><th>Email</th><th>Phone</th><th>Website</th><th>Domain</th><th>City</th><th>Full address</th><th>Email source</th><th>Verification</th><th>Tool</th><th>Quality flags</th></tr></thead><tbody>
        {(pageRows as Rejected[]).map(item => <tr key={`reject-${item.row}`}><td>{item.row}</td><td><span className={`badge ${item.status}`}>{item.status}</span></td>{leadColumns.map(([key]) => <td key={key}>{item.data?.[key] || "—"}</td>)}</tr>)}
      </tbody></table></div>
    </>}
    {page && page.total > 0 && <div className="batch-pagination"><button className="secondary-button" disabled={offset === 0 || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>← Previous</button><span>Page {Math.floor(offset / 50) + 1} of {Math.ceil(page.total / 50)}</span><button className="secondary-button" disabled={!page.has_more || busy} onClick={() => setOffset(offset + 50)}>Next →</button></div>}
  </section>;
}
