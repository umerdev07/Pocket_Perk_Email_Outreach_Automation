"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { apiFetch, WorkspaceRole } from "../../lib/auth";

type TeamMember = {
  id: string;
  email: string;
  role: WorkspaceRole;
  is_active: boolean;
  access_removed_by_admin?: boolean;
  created_at: string;
  last_login_at: string | null;
  pending_invite?: boolean;
  pending_role?: WorkspaceRole | null;
  expires_at?: string;
};

type MemberAction = "remove" | "restore" | "permanent-delete";
type PendingConfirmation = { member: TeamMember; action: MemberAction } | null;

const roleNames: Record<WorkspaceRole, string> = {
  admin: "Admin",
  operator: "Operator",
  reviewer: "Reviewer",
};

export default function TeamManagement({ currentUserId }: { currentUserId: string }) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("operator");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyUser, setBusyUser] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmation, setConfirmation] = useState<PendingConfirmation>(null);
  const cancelConfirmationRef = useRef<HTMLButtonElement>(null);

  async function loadMembers() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/admin/users");
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not load team members.");
      setMembers(data.items || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load team members.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadMembers(); }, []);

  useEffect(() => {
    if (!confirmation) return;
    cancelConfirmationRef.current?.focus();
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setConfirmation(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [confirmation]);

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const response = await apiFetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not add this team member.");
      setEmail("");
      setRole("operator");
      setNotice(`Invitation sent to ${data.email}. They must accept it within 48 hours.`);
      await loadMembers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add this team member.");
    } finally {
      setBusy(false);
    }
  }

  async function updateMember(member: TeamMember, changes: { role?: WorkspaceRole; is_active?: boolean }) {
    setError("");
    setNotice("");
    setBusyUser(member.id);
    try {
      const response = await apiFetch(`/api/admin/users/${member.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not update this team member.");
      setNotice(data.pending_role
        ? `Confirmation sent to ${member.email}. Their current role stays active until they confirm.`
        : data.notification_sent === false
          ? `${member.email}'s access was updated, but the notice email could not be sent.`
          : `${member.email} was updated.`);
      await loadMembers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update this team member.");
    } finally {
      setBusyUser("");
    }
  }

  async function removeMember(member: TeamMember) {
    setError("");
    setNotice("");
    setBusyUser(member.id);
    try {
      const response = await apiFetch(`/api/admin/users/${member.id}`, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not remove this team member.");
      setNotice(`${member.email} no longer has workspace access.`);
      await loadMembers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove this team member.");
    } finally { setBusyUser(""); }
  }

  async function permanentlyDeleteMember(member: TeamMember) {
    setError("");
    setNotice("");
    setBusyUser(member.id);
    try {
      const response = await apiFetch(`/api/admin/users/${member.id}/permanent`, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not permanently delete this team member.");
      setNotice(`${member.email}'s account was permanently deleted. Shared workspace records were kept.`);
      await loadMembers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not permanently delete this team member.");
    } finally { setBusyUser(""); }
  }

  async function cancelInvitation(member: TeamMember) {
    setError("");
    setNotice("");
    setBusyUser(member.id);
    try {
      const response = await apiFetch(`/api/admin/users/invitations/${member.id}`, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not cancel this invitation.");
      setNotice(`Invitation for ${member.email} was cancelled.`);
      await loadMembers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not cancel this invitation.");
    } finally { setBusyUser(""); }
  }

  async function confirmMemberAction() {
    if (!confirmation) return;
    const { member, action } = confirmation;
    setConfirmation(null);
    if (action === "remove") await removeMember(member);
    else if (action === "restore") await updateMember(member, { is_active: true });
    else await permanentlyDeleteMember(member);
  }

  const confirmationCopy = confirmation?.action === "remove"
    ? { title: "Remove workspace access?", message: `${confirmation.member.email} will no longer be able to sign in. You can restore access later.`, button: "Remove access" }
    : confirmation?.action === "restore"
      ? { title: "Restore workspace access?", message: `${confirmation.member.email} will be able to sign in to this workspace again.`, button: "Restore access" }
      : confirmation
        ? { title: "Permanently delete this account?", message: `${confirmation.member.email}'s account and login will be deleted and cannot be restored. Shared campaigns, leads, and review history will remain.`, button: "Delete permanently" }
        : null;

  return <section className="team-management" aria-labelledby="team-title">
    <div className="team-heading">
      <div><h2 id="team-title">Team access</h2><p>Manage employees in this shared PocketPerks workspace.</p></div>
      <button type="button" className="secondary-button" onClick={() => void loadMembers()} disabled={loading}>Refresh</button>
    </div>
    {error && <p className="auth-error" role="alert">{error}</p>}
    {notice && <p className="auth-success" role="status">{notice}</p>}
    <form className="team-add-form" onSubmit={event => void addMember(event)}>
      <label>Employee email<input type="email" autoComplete="off" required maxLength={320} value={email} onChange={event => setEmail(event.target.value)} /></label>
      <label>Role<select value={role} onChange={event => setRole(event.target.value as WorkspaceRole)}><option value="operator">Operator</option><option value="reviewer">Reviewer</option><option value="admin">Admin</option></select></label>
      <button className="action-button" disabled={busy}>{busy ? "Sending invitation…" : "Send invitation"}</button>
      <small className="team-invite-note">The employee will receive an invitation email and choose their own password.</small>
    </form>
    <div className="team-member-list">
      {loading ? <p>Loading team members…</p> : members.length === 0 ? <p>No team members found.</p> : members.map(member => {
        const isSelf = member.id === currentUserId;
        if (member.pending_invite) return <article className="team-member" key={member.id}>
          <div className="team-member-info"><b>{member.email}</b><small>Invitation pending · expires {member.expires_at ? new Date(member.expires_at).toLocaleString() : "soon"}</small></div>
          <span className="team-role-control">{roleNames[member.role]}</span>
          <button type="button" className="secondary-button" disabled={busyUser === member.id} onClick={() => void cancelInvitation(member)}>Cancel invitation</button>
        </article>;
        return <article className="team-member" key={member.id}>
          <div className="team-member-info"><b>{member.email}{isSelf ? " · You" : ""}</b><small className={!member.is_active && member.access_removed_by_admin ? "team-removed-status" : ""}>{member.is_active ? "Active" : member.access_removed_by_admin ? "Access removed by admin" : "Access paused"} · Added {new Date(member.created_at).toLocaleDateString()}</small></div>
          <label className="team-role-control">Role<select aria-label={`Role for ${member.email}`} disabled={isSelf || busyUser === member.id || !member.is_active} value={member.role} onChange={event => void updateMember(member, { role: event.target.value as WorkspaceRole })}>{Object.entries(roleNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>{member.pending_role && <small>Waiting for email confirmation: {roleNames[member.pending_role]}</small>}</label>
          <div className="team-actions">
            {member.is_active
              ? <button type="button" className="team-icon-button" aria-label={`Pause access for ${member.email}`} title="Pause access" disabled={isSelf || busyUser === member.id} onClick={() => void updateMember(member, { is_active: false })}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14" /></svg></button>
              : <button type="button" className="team-icon-button" aria-label={`Restore access for ${member.email}`} title="Restore access" disabled={busyUser === member.id} onClick={() => setConfirmation({ member, action: "restore" })}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4.8 9A8 8 0 0 1 19 6l1 1M4 17l1-1a8 8 0 0 0 14.2-3" /></svg></button>}
            {member.is_active && <button type="button" className="team-icon-button team-remove-button" aria-label={`Remove ${member.email}`} title="Remove user" disabled={isSelf || busyUser === member.id} onClick={() => setConfirmation({ member, action: "remove" })}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0M17 9h5" /></svg></button>}
            {!isSelf && <button type="button" className="team-icon-button team-permanent-delete-button" aria-label={`Permanently delete ${member.email}`} title="Permanently delete account" disabled={busyUser === member.id} onClick={() => setConfirmation({ member, action: "permanent-delete" })}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3" /></svg></button>}
          </div>
        </article>;
      })}
    </div>
    <small className="team-note">All team members see the same company records. At least one active admin must remain.</small>
    {confirmation && confirmationCopy && <div className="team-confirm-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setConfirmation(null); }}>
      <section className="team-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="team-confirm-title" aria-describedby="team-confirm-message">
        <span className={`team-confirm-icon${confirmation.action === "permanent-delete" ? " is-danger" : ""}`} aria-hidden="true">{confirmation.action === "restore" ? "↻" : "!"}</span>
        <h3 id="team-confirm-title">{confirmationCopy.title}</h3>
        <p id="team-confirm-message">{confirmationCopy.message}</p>
        <div className="team-confirm-actions">
          <button type="button" className="secondary-button" ref={cancelConfirmationRef} onClick={() => setConfirmation(null)}>Cancel</button>
          <button type="button" className={confirmation.action === "permanent-delete" ? "team-confirm-danger" : "action-button"} onClick={() => void confirmMemberAction()}>{confirmationCopy.button}</button>
        </div>
      </section>
    </div>}
  </section>;
}
