import type { Metadata } from "next";
import AuthGate from "./components/auth-gate";
import "./globals.css";

export const metadata: Metadata = {
  title: "PocketPerks | Outreach workspace",
  description: "Import and check business leads, prepare and review campaign emails, manage sending schedules, and handle replies in one shared workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body suppressHydrationWarning><AuthGate>{children}</AuthGate></body></html>;
}
