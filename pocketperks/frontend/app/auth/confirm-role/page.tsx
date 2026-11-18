"use client";

import { useEffect, useRef, useState } from "react";
import AuthFrame from "../../components/auth-frame";

export default function ConfirmRolePage() {
  const [message, setMessage] = useState("Checking your confirmation link…");
  const [failed, setFailed] = useState(false);
  const confirmationStarted = useRef(false);

  useEffect(() => {
    if (confirmationStarted.current) return;
    confirmationStarted.current = true;
    const url = new URL(window.location.href);
    const token = url.searchParams.get("token");
    window.history.replaceState(null, "", window.location.pathname);
    if (!token) {
      setFailed(true);
      setMessage("This role confirmation link is invalid or expired. Ask your workspace admin to send a new one.");
      return;
    }
    void fetch("/api/auth/role-change/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
    }).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not confirm this role change.");
      setMessage(`Your role is now ${data.role}. For security, sign in again to refresh your access.`);
    }).catch(cause => {
      setFailed(true);
      setMessage(cause instanceof Error ? cause.message : "Could not confirm this role change.");
    });
  }, []);

  return <AuthFrame><section className="auth-card" aria-labelledby="confirm-role-title">
    <small className="auth-card-kicker">ACCOUNT SECURITY</small>
    <h1 id="confirm-role-title">Confirm workspace role</h1>
    <p className={failed ? "auth-error" : "auth-success"} role={failed ? "alert" : "status"}>{message}</p>
    <a className="action-button auth-return" href="/login">Continue to sign in</a>
  </section></AuthFrame>;
}
