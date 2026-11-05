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
      if (!response.ok) throw new Error(data.detail || "Could not load suppression records.");
      setItems(Array.isArray(data.items) ? data.items as Suppression[] : []);
      setEvents(Array.isArray(data.events) ? data.events as AuditEvent[] : []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load suppression records.");
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
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(apiBase + "/api/suppressions/" + encodeURIComponent(email) + "/release", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, recipient_confirmed: recipientConfirmed }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not release this address.");
      setReleaseEmail("");
      setReason("");
      setRecipientConfirmed(false);
      setNotice(email + " was released from suppression. The release is recorded in the audit history.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not release this address.");
    } finally {
      setBusy(false);
    }
  }

  const activeCount = items.filter(item => item.active).length;
  return <section className="card suppression-manager" aria-label="Suppression management">
    <div className="suppression-manager-heading">
      <div><small className="eyebrow">ADMIN CONTROLS</small><h2>Email suppression</h2>
        <p>Opted-out addresses are skipped for future imports, personalization and Instantly sync.</p></div>
      <button type="button" className="secondary-button" onClick={() => void toggle()}>{open ? "Hide list" : "Manage list"}</button>
    </div>
    {open && <>
      <div className="suppression-toolbar"><span><b>{activeCount}</b> active · {items.length} addresses loaded</span>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => void load()}>{loading ? "Refreshing…" : "Refresh"}</button></div>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="success">{notice}</p>}
      {loading ? <p>Loading suppression list…</p> : items.length === 0 ? <p>No suppression records yet.</p> :
        <div className="table suppression-table"><table><thead><tr><th>Email</th><th>Status</th><th>Reason</th><th>Recorded</th><th>Action</th></tr></thead><tbody>
          {items.map(item => <tr key={item.email}><td>{item.email}</td><td>{item.active ? "Suppressed" : "Released"}</td>
            <td>{item.active ? item.reason : item.release_reason || item.reason}</td>
            <td>{new Date(item.active ? item.created_at : item.released_at || item.created_at).toLocaleString()}</td>
            <td>{item.active ? <button type="button" className="secondary-button" onClick={() => {
              setReleaseEmail(item.email); setReason(""); setRecipientConfirmed(false); setError(""); setNotice("");
            }}>Allow future emails</button> : "—"}</td></tr>)}
        </tbody></table></div>}
      {releaseEmail && <form className="suppression-release-form" onSubmit={event => { event.preventDefault(); void release(releaseEmail); }}>
        <h3>Release {releaseEmail}</h3>
        <p>Only continue if the recipient explicitly asked to receive future emails again. This does not add them to an active Instantly campaign.</p>
        <label>Audit reason<textarea required minLength={10} maxLength={300} value={reason} onChange={event => setReason(event.target.value)} placeholder="How and when did the recipient confirm re-subscription?" rows={2}/></label>
        <label className="suppression-consent"><input type="checkbox" checked={recipientConfirmed} onChange={event => setRecipientConfirmed(event.target.checked)}/>
          The recipient explicitly requested to receive PocketPerks emails again.</label>
        <div><button type="button" className="secondary-button" onClick={() => setReleaseEmail("")}>Cancel</button>
          <button type="submit" className="action-button" disabled={busy || !recipientConfirmed || reason.trim().length < 10}>{busy ? "Saving…" : "Release suppression"}</button></div>
      </form>}
      <details className="suppression-audit"><summary>Recent suppression history ({events.length})</summary>
        {events.length === 0 ? <p>No audit events yet.</p> : <div className="table suppression-table"><table><thead><tr><th>When</th><th>Email</th><th>Action</th><th>Reason</th><th>By</th></tr></thead><tbody>
          {events.map(event => <tr key={event.id}><td>{new Date(event.created_at).toLocaleString()}</td><td>{event.email}</td>
            <td>{event.event_type === "released" ? "Released" : "Suppressed"}</td><td>{event.reason}</td><td>{event.actor_user_id ? "Workspace user " + event.actor_user_id.slice(0, 8) : "Automatic"}</td></tr>)}
        </tbody></table></div>}
      </details>
    </>}
  </section>;
}
