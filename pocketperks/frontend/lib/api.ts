export type Health = {
  status: "ok" | "degraded";
  backend: "ok";
  database: "connected" | "unavailable";
  send_mode: "test" | "live";
  detail?: string;
};

export async function getHealth(): Promise<Health> {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const response = await fetch(`${base.replace(/\/$/, "")}/api/health`, {
    cache: "no-store", signal: AbortSignal.timeout(15000),
  });
  if (!response.ok && response.status !== 503) throw new Error("Backend health check failed.");
  return response.json();
}
