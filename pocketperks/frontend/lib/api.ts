export type Health = {
  status: "ok" | "degraded";
  backend: "ok";
  database: "connected" | "unavailable";
  send_mode: "test" | "live";
  detail?: string;
};

const technicalErrorPattern = /\b(?:instantly|supabase|claude|openrouter|fastapi|postgrest|api|http|sql|database|backend|webhook|schema|endpoint|cors|provider|service role|access token|refresh token)\b|NEXT_PUBLIC_[A-Z0-9_]+|\.env(?:\.local)?|localhost(?::\d+)?|https?:\/\/|traceback|stack trace/i;

export function userFacingError(value: unknown, fallback = "Something went wrong. Please try again or contact your workspace admin."): string {
  if (typeof value !== "string" || !value.trim() || value.length > 400 || technicalErrorPattern.test(value)) return fallback;
  return value;
}

export async function hideTechnicalErrorDetails(response: Response): Promise<Response> {
  if (response.ok) return response;
  const payload = await response.clone().json().catch(() => null) as Record<string, unknown> | null;
  if (!payload || typeof payload !== "object") return response;
  let changed = false;
  for (const key of ["detail", "message", "msg", "error", "error_description"]) {
    if (typeof payload[key] !== "string") continue;
    const safe = userFacingError(payload[key]);
    if (safe !== payload[key]) {
      payload[key] = safe;
      changed = true;
    }
  }
  if (!changed) return response;
  const headers = new Headers(response.headers);
  headers.delete("content-length");
  headers.delete("content-encoding");
  return new Response(JSON.stringify(payload), { status: response.status, statusText: response.statusText, headers });
}

export async function getHealth(): Promise<Health> {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const response = await fetch(`${base.replace(/\/$/, "")}/api/health`, {
    cache: "no-store", signal: AbortSignal.timeout(15000),
  });
  if (!response.ok && response.status !== 503) throw new Error("Backend health check failed.");
  return response.json();
}
