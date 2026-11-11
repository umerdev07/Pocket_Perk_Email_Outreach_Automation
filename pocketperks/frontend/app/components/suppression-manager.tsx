"use client";

import { useState } from "react";
import { apiFetch } from "../../lib/auth";

type Suppression = {
  email: string; reason: string; source: string; active: boolean; created_at: string;
  released_at?: string | null; released_by?: string | null; release_reason?: string | null;
};
type AuditEvent = { id: string; email: string; event_type: "suppressed" | "released"; source: string;
  reason: string; actor_user_id?: string | null; created_at: string };
type Props = { apiBase: string };

export default function SuppressionManager({ apiBase }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [items, setItems] = useState<Suppression[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [releaseEmail, setReleaseEmail] = useState("");
  const [reason, setReason] = useState("");
  const [recipientConfirmed, setRecipientConfirmed] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(apiBase + "/api/suppressions?limit=100&offset=0");
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not load the do-not-email list.");
      setItems(Array.isArray(data.items) ? data.items as Suppression[] : []);
      setEvents(Array.isArray(data.events) ? data.events as AuditEvent[] : []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the do-not-email list.");
    } finally {
      setLoading(false);
    }
  }

  async function toggle() {
    const nextOpen = !open;
    setOpen(nextOpen);
    setNotice("");
    if (nextOpen) await load();
  }

  async function release(email: string) {
    const selected = items.find(item => item.email === email);
    const isBounce = selected?.source === "bounce_auto";
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(apiBase + "/api/suppressions/" + encodeURIComponent(email) + "/release", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, recipient_confirmed: isBounce ? false : recipientConfirmed,
          bounce_address_corrected: isBounce ? recipientConfirmed : false }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not allow future emails to this address.");
      setReleaseEmail("");
      setReason("");
      setRecipientConfirmed(false);
      setNotice(email + " can receive future emails again. This change is recorded in the history.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not allow future emails to this address.");
    } finally {
      setBusy(false);
    }
  }

  const activeCount = items.filter(item => item.active).length;
  return <section className="card suppression-manager" aria-label="Do-not-email list management">
    <div className="suppression-manager-heading">
      <div><small className="eyebrow">ADMIN CONTROLS</small><h2>Do-not-email list</h2>
        <p>Opt-outs and unsafe bounced addresses are blocked from future campaigns.</p></div>
      <button type="button" className="secondary-button" onClick={() => void toggle()}>{open ? "Hide list" : "Manage list"}</button>
    </div>
    {open && <>
      <div className="suppression-toolbar"><span><b>{activeCount}</b> active · {items.length} addresses loaded</span>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => void load()}>{loading ? "Refreshing…" : "Refresh"}</button></div>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="success">{notice}</p>}
      {loading ? <p>Loading do-not-email list…</p> : items.length === 0 ? <p>No blocked email addresses yet.</p> :
        <div className="table suppression-table"><table><thead><tr><th>Email</th><th>Status</th><th>Reason</th><th>Recorded</th><th>Action</th></tr></thead><tbody>
          {items.map(item => <tr key={item.email}><td>{item.email}</td><td>{item.active ? item.source === "bounce_auto" ? "Bounce block" : "Opted out" : "Allowed"}</td>
            <td>{item.active ? item.reason : item.release_reason || item.reason}</td>
            <td>{new Date(item.active ? item.created_at : item.released_at || item.created_at).toLocaleString()}</td>
            <td>{item.active ? <button type="button" className="secondary-button" onClick={() => {
              setReleaseEmail(item.email); setReason(""); setRecipientConfirmed(false); setError(""); setNotice("");
            }}>Allow future emails</button> : "—"}</td></tr>)}
        </tbody></table></div>}
      {releaseEmail && <form className="suppression-release-form" onSubmit={event => { event.preventDefault(); void release(releaseEmail); }}>
        <h3>Release {releaseEmail}</h3>
        <p>{items.find(item => item.email === releaseEmail)?.source === "bounce_auto"
          ? "Only clear this bounce block after correcting the address and verifying it again. This does not add the lead to a campaign that is already running."
          : "Only continue if the recipient explicitly asked to receive future emails again. This does not add them to a campaign that is already running."}</p>
        <label>Audit reason<textarea required minLength={10} maxLength={300} value={reason} onChange={event => setReason(event.target.value)} placeholder={items.find(item => item.email === releaseEmail)?.source === "bounce_auto" ? "What was corrected and how was the address re-verified?" : "How and when did the recipient confirm re-subscription?"} rows={2}/></label>
        <label className="suppression-consent"><input type="checkbox" checked={recipientConfirmed} onChange={event => setRecipientConfirmed(event.target.checked)}/>
          {items.find(item => item.email === releaseEmail)?.source === "bounce_auto"
            ? "I corrected this address and verified it is deliverable."
            : "The recipient explicitly requested to receive PocketPerks emails again."}</label>
        <div><button type="button" className="secondary-button" onClick={() => setReleaseEmail("")}>Cancel</button>
          <button type="submit" className="action-button" disabled={busy || !recipientConfirmed || reason.trim().length < 10}>{busy ? "Saving…" : "Allow future emails"}</button></div>
      </form>}
      <details className="suppression-audit"><summary>Recent opt-out history ({events.length})</summary>
        {events.length === 0 ? <p>No audit events yet.</p> : <div className="table suppression-table"><table><thead><tr><th>When</th><th>Email</th><th>Action</th><th>Reason</th><th>By</th></tr></thead><tbody>
          {events.map(event => <tr key={event.id}><td>{new Date(event.created_at).toLocaleString()}</td><td>{event.email}</td>
            <td>{event.event_type === "released" ? "Allowed" : "Opted out"}</td><td>{event.reason}</td><td>{event.actor_user_id ? "Workspace admin" : "Automatic"}</td></tr>)}
        </tbody></table></div>}
      </details>
    </>}
  </section>;
}
