import type { Metadata } from "next";
import AuthGate from "./components/auth-gate";
import "./globals.css";

export const metadata: Metadata = { title: "PocketPerks", description: "PocketPerks outreach" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AuthGate>{children}</AuthGate></body></html>;
}
