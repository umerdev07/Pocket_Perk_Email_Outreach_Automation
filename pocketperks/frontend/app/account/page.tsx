"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import AuthFrame from "../components/auth-frame";
import { useWorkspaceAuth } from "../components/auth-gate";
import { requestEmailChange } from "../../lib/auth";

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

  return <AuthFrame><section className="auth-card" aria-labelledby="account-title">
    <small className="auth-card-kicker">ACCOUNT SETTINGS</small>
    <h1 id="account-title">Change login email</h1>
    <p>Signed in as {user.email}. Your admin access and password will stay the same.</p>
    {error && <p className="auth-error" role="alert">{error}</p>}
    {sent ? <p className="auth-success" role="status">Confirmation links have been sent. Confirm the change from the email messages to finish updating your login email.</p> : <form onSubmit={submit}>
      <label>New email address<input type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} /></label>
      <button className="action-button" disabled={busy}>{busy ? "Sending confirmation…" : "Send confirmation links"}</button>
    </form>}
    <div className="auth-card-foot"><span className="auth-lock">⌑</span><small>For security, you may need to confirm the change from both your current and new email inboxes. Your password is not changed.</small></div>
    <Link className="auth-reset-link account-back-link" href="/">Back to PocketPerks</Link>
  </section></AuthFrame>;
}
