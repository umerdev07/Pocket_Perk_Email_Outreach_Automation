"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import AuthFrame from "../components/auth-frame";
import { useWorkspaceAuth } from "../components/auth-gate";
import { requestEmailChange } from "../../lib/auth";
import TeamManagement from "./team-management";

const roleLabels = { admin: "Admin", operator: "Operator", reviewer: "Reviewer" } as const;

export default function AccountSettingsPage() {
  const { user } = useWorkspaceAuth();
  const [email, setEmail] = useState(user.email);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSent(false);
    const nextEmail = email.trim().toLowerCase();
    if (!nextEmail || nextEmail === user.email.toLowerCase()) {
      setError("Enter a different email address.");
      return;
    }

    setBusy(true);
    try {
      await requestEmailChange(nextEmail);
      setSent(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not request the email change.");
    } finally {
      setBusy(false);
    }
  }

  return <AuthFrame wide><div className="account-settings-layout">
    <header className="account-page-heading">
      <small className="auth-card-kicker">ACCOUNT</small>
      <h1 id="account-title">Account settings</h1>
      <p>Signed in as <b>{user.email}</b><span className="account-role-badge">{roleLabels[user.role]}</span></p>
      {user.role === "admin" && <a className="account-team-jump" href="#team-title">Manage team roles ↓</a>}
    </header>
    {user.role === "admin" ? <TeamManagement currentUserId={user.id} /> : <section className="account-role-card" aria-label="Workspace role">
      <b>Your workspace role is {roleLabels[user.role]}.</b>
      <span>Roles are managed by a PocketPerks workspace admin. All team members work with the same company records.</span>
    </section>}
    <section className="auth-card account-email-card" id="login-email" aria-labelledby="email-title">
    <small className="auth-card-kicker">LOGIN EMAIL</small>
    <h2 id="email-title">Change login email</h2>
    <p>Your workspace role and password will stay the same.</p>
    {error && <p className="auth-error" role="alert">{error}</p>}
    {sent ? <p className="auth-success" role="status">Confirmation links have been sent. Confirm the change from the email messages to finish updating your login email.</p> : <form onSubmit={submit}>
      <label>New email address<input type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} /></label>
      <button className="action-button" disabled={busy}>{busy ? "Sending confirmation…" : "Send confirmation links"}</button>
    </form>}
    <div className="auth-card-foot"><span className="auth-lock">⌑</span><small>For security, you may need to confirm the change from both your current and new email inboxes. Your password is not changed.</small></div>
    <Link className="auth-reset-link account-back-link" href="/workspace">← Back to dashboard</Link>
    </section>
  </div></AuthFrame>;
}
