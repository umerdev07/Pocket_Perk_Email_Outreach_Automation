"use client";

import { useEffect, useState } from "react";
import AuthFrame from "../../components/auth-frame";

export default function ConfirmEmailPage() {
  const [message, setMessage] = useState("Checking your confirmation link…");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
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
        ? "Both addresses are confirmed. Your login email has been updated. Please sign in again."
        : "This address is confirmed. Open the confirmation link sent to the other email address to finish the change.");
    }).catch(cause => {
      setFailed(true);
      setMessage(cause instanceof Error ? cause.message : "Could not confirm this email change.");
    });
  }, []);

  return <AuthFrame><section className="auth-card" aria-labelledby="confirm-email-title">
    <small className="auth-card-kicker">ACCOUNT SECURITY</small>
    <h1 id="confirm-email-title">Confirm login email change</h1>
    <p className={failed ? "auth-error" : "auth-success"} role={failed ? "alert" : "status"}>{message}</p>
    <a className="action-button auth-return" href="/">Return to sign in</a>
  </section></AuthFrame>;
}
