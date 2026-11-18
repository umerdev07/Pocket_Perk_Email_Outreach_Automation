"use client";

import { FormEvent, useEffect, useState } from "react";
import AuthFrame from "../../components/auth-frame";

export default function AcceptInvitationPage() {
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tokenChecked, setTokenChecked] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    setToken(url.searchParams.get("token") || "");
    setTokenChecked(true);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password.length < 12) { setError("Use a password with at least 12 characters."); return; }
    if (password !== confirmation) { setError("The passwords do not match."); return; }
    setBusy(true);
    try {
      const response = await fetch("/api/auth/invitations/accept", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not accept this invitation.");
      setEmail(data.email || "");
      setRole(data.role || "");
      setPassword("");
      setConfirmation("");
      setToken("");
      setSuccess(true);
      window.history.replaceState(window.history.state, "", window.location.pathname);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not accept this invitation.");
    } finally { setBusy(false); }
  }

  return <AuthFrame><section className="auth-card" aria-labelledby="invite-title">
    <small className="auth-card-kicker">WORKSPACE INVITATION</small>
    <h1 id="invite-title">Join PocketPerks</h1>
    {!tokenChecked ? <p role="status">Checking your invitation link…</p> : success ? <><p className="auth-success" role="status">{email} is now set up as {role}. You can sign in with your new password.</p><a className="action-button auth-return" href="/login">Go to sign in</a></> : token ? <>
      <p>Set a password to accept your invitation. This link can only be used once.</p>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <form onSubmit={event => void submit(event)}>
        <label>Password<input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={event => setPassword(event.target.value)} /></label>
        <label>Confirm password<input type="password" autoComplete="new-password" minLength={12} required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
        <button className="action-button" disabled={busy}>{busy ? "Setting up your access…" : "Accept invitation"}</button>
      </form>
    </> : <><p className="auth-error" role="alert">This invitation link is invalid or expired. Ask your workspace admin to send a new one.</p><a className="auth-reset-link" href="/login">Back to sign in</a></>}
  </section></AuthFrame>;
}
