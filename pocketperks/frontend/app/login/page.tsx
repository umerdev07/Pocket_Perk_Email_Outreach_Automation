import Link from "next/link";

export default function LoginPage() {
  return <div className="login-route-note"><span>✓</span><p>You’re signed in already.</p><Link href="/workspace">Open workspace →</Link></div>;
}
