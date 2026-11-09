"use client";

import { FormEvent, useEffect, useState } from "react";
import { setNewPassword } from "../../../lib/auth";
import AuthFrame from "../../components/auth-frame";

export default function ResetPasswordPage() {
  const [accessToken, setAccessToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
    const token = fragment.get("access_token") || url.searchParams.get("access_token") || "";
    const type = fragment.get("type") || url.searchParams.get("type") || "";
    if (token && type === "recovery") setAccessToken(token);
    else setError("This password reset link is invalid or expired. Request a new link from the sign-in page.");
    window.history.replaceState(null, "", window.location.pathname);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password.length < 8) { setError("Use a password with at least 8 characters."); return; }
    if (password !== confirmation) { setError("The passwords do not match."); return; }
    setBusy(true);
    try {
      await setNewPassword(accessToken, password);
      setPassword("");
      setConfirmation("");
      setSuccess(true);
      setAccessToken("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update the password.");
    } finally { setBusy(false); }
  }

  return <AuthFrame><section className="auth-card" aria-labelledby="reset-title">
    <small className="auth-card-kicker">ACCOUNT SECURITY</small>
    <h1 id="reset-title">Set a new password</h1>
    {success ? <><p className="auth-success" role="status">Your password has been updated. Sign in using your email and new password.</p><a className="action-button auth-return" href="/">Return to sign in</a></> : accessToken ? <>
      <p>Choose a new password for your PocketPerks workspace account.</p>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <form onSubmit={submit}>
        <label>New password<input type="password" autoComplete="new-password" minLength={8} required value={password} onChange={event=>setPassword(event.target.value)} /></label>
        <label>Confirm new password<input type="password" autoComplete="new-password" minLength={8} required value={confirmation} onChange={event=>setConfirmation(event.target.value)} /></label>
        <button className="action-button" disabled={busy}>{busy ? "Updating password…" : "Update password"}</button>
      </form>
    </> : <><p className="auth-error" role="alert">{error || "Checking your reset link…"}</p><a className="auth-reset-link" href="/">Back to sign in</a></>}
  </section></AuthFrame>;
}
