"use client";

import { useEffect, useRef, useState } from "react";
import AuthFrame from "../../components/auth-frame";

export default function ConfirmEmailPage() {
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
      setMessage("This confirmation link is invalid or expired. Request a new email change from account settings.");
      return;
    }
    void fetch("/api/auth/email-change/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
    }).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not confirm this email change.");
      setMessage(data.complete
        ? `Both addresses are confirmed. Your login email is now ${data.email}. Your password is unchanged. Sign in again with the new email and your existing password.`
        : `Confirmed ${data.confirmed_email}. One more confirmation is required at ${data.pending_email}. Your login email is still ${data.current_email} until both addresses are confirmed.`);
    }).catch(cause => {
      setFailed(true);
      setMessage(cause instanceof Error ? cause.message : "Could not confirm this email change.");
    });
  }, []);

  return <AuthFrame><section className="auth-card" aria-labelledby="confirm-email-title">
    <small className="auth-card-kicker">ACCOUNT SECURITY</small>
    <h1 id="confirm-email-title">Confirm login email change</h1>
    <p className={failed ? "auth-error" : "auth-success"} role={failed ? "alert" : "status"}>{message}</p>
    <a className="action-button auth-return" href={message.includes("Both addresses are confirmed") ? "/login?notice=email-changed" : "/login"}>Go to sign in</a>
  </section></AuthFrame>;
}
