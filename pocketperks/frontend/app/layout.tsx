import type { Metadata } from "next";
import AuthGate from "./components/auth-gate";
import "./globals.css";

export const metadata: Metadata = {
  title: "PocketPerks | Thoughtful local outreach",
  description: "A clear workspace for preparing PocketPerks emails, reviewing every message, and managing replies.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AuthGate>{children}</AuthGate></body></html>;
}
