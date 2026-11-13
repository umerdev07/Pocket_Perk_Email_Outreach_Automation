"use client";

import { createContext, FormEvent, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiFetch, AuthSession, currentSession, requestPasswordReset, signIn, signOut, WorkspaceRole } from "../../lib/auth";
import AuthFrame from "./auth-frame";

type WorkspaceUser = { id: string; email: string; role: WorkspaceRole };
type AuthContextValue = { user: WorkspaceUser; logout: () => Promise<void> };
const AuthContext = createContext<AuthContextValue | null>(null);

export function useWorkspaceAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useWorkspaceAuth must be used inside AuthGate.");
  return value;
}

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<WorkspaceUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoverySent, setRecoverySent] = useState(false);
  const [passwordResetPath, setPasswordResetPath] = useState(false);
  const [error, setError] = useState("");

  async function loadWorkspaceUser(_session: AuthSession | null) {
    if (!_session) { setUser(null); return; }
    const response = await apiFetch("/api/auth/me");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error("We couldn't verify your workspace access. Please sign in again or contact your workspace admin.");
    setUser(data as WorkspaceUser);
  }

  useEffect(() => {
    let active = true;
    if (window.location.pathname === "/auth/reset-password") {
      setPasswordResetPath(true);
      setLoading(false);
      return () => { active = false; };
    }
    void currentSession().then(async session => {
      if (session) await loadWorkspaceUser(session);
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "Could not restore your sign-in.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const session = await signIn(email.trim(), password);
      await loadWorkspaceUser(session);
      setPassword("");
      if (pathname === "/login") router.push("/workspace");
    } catch (cause) {
      await signOut();
      setUser(null);
      setError(cause instanceof Error ? cause.message : "Sign-in failed.");
    } finally { setBusy(false); }
  }

  async function sendRecoveryLink() {
    setError("");
    setRecoverySent(false);
    setRecoveryBusy(true);
    try {
      await requestPasswordReset(email.trim());
      setRecoverySent(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not request a password reset.");
    } finally { setRecoveryBusy(false); }
  }

  async function logout() {
    await signOut();
    setUser(null);
  }

  if (pathname === "/") return children;
  if (loading) return <AuthFrame><section className="auth-card auth-loading"><span className="auth-spinner" /><p>Checking your workspace sign-in…</p></section></AuthFrame>;
  if (passwordResetPath) return children;
  if (user) return <AuthContext.Provider value={{ user, logout }}>{children}</AuthContext.Provider>;

  return <AuthFrame><section className="auth-card" aria-labelledby="auth-title">
    <small className="auth-card-kicker">WELCOME BACK</small>
    <h1 id="auth-title">Sign in to your workspace</h1>
    <p>Use the email address invited to PocketPerks.</p>
    {error && <p className="auth-error" role="alert">{error}</p>}
    <form onSubmit={submit}>
      <label>Email<input type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} /></label>
      <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <button className="action-button" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
    <button className="auth-reset-link" type="button" disabled={!email.trim() || recoveryBusy} onClick={()=>void sendRecoveryLink()}>{recoveryBusy ? "Sending reset link…" : "Forgot password? Send a reset link"}</button>
    {recoverySent && <p className="auth-success" role="status">If that account exists, a password reset link has been sent to its email.</p>}
    <div className="auth-card-foot"><span className="auth-lock">⌑</span><small>Invite-only access. Need an account? Ask your workspace admin.</small></div>
  </section></AuthFrame>;
}
