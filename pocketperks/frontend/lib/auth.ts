import { hideTechnicalErrorDetails } from "./api";

export type WorkspaceRole = "admin" | "operator" | "reviewer";

export type AuthSession = {
  access_token: string;
  expires_at: number;
  user: { id: string; email?: string };
};

let refreshInProgress: Promise<AuthSession | null> | null = null;
let memorySession: AuthSession | null = null;

function apiBase() {
  return "";
}

async function readResponse(response: Response, fallback: string) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data.detail === "string" ? data.detail : fallback;
    throw new Error(message);
  }
  return data;
}

async function postAuth(path: string, body: Record<string, string>, token?: string) {
  let response: Response;
  try {
    response = await fetch(`${apiBase()}/api/auth/${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("We couldn't connect to sign-in. Check your internet connection and try again.");
  }
  return readResponse(response, "We couldn't complete that sign-in request. Please try again.");
}

function asSession(data: Record<string, unknown>): AuthSession {
  return {
    access_token: String(data.access_token || ""),
    expires_at: Number(data.expires_at || Math.floor(Date.now() / 1000) + Number(data.expires_in || 900)),
    user: data.user as AuthSession["user"],
  };
}

export async function signIn(email: string, password: string): Promise<AuthSession> {
  const session = asSession(await postAuth("sign-in", { email, password }));
  if (!session.access_token || !session.user?.id) {
    throw new Error("We couldn't verify your workspace sign-in. Please try again.");
  }
  memorySession = session;
  return session;
}

export async function requestPasswordReset(email: string): Promise<void> {
  await postAuth("password-reset", { email });
}

export async function setNewPassword(token: string, password: string): Promise<void> {
  await postAuth("password-reset/confirm", { token, password });
}

export async function requestEmailChange(email: string): Promise<void> {
  const response = await apiFetch(`${apiBase()}/api/auth/email-change`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }),
  });
  await readResponse(response, "We couldn't request the email change. Check the address and try again.");
}

async function refreshSession(): Promise<AuthSession | null> {
  if (!refreshInProgress) {
    refreshInProgress = postAuth("refresh", {})
      .then(data => {
        const next = asSession(data);
        if (!next.access_token || !next.user?.id) throw new Error("Invalid session response");
        memorySession = next;
        return next;
      })
      .catch(() => {
        memorySession = null;
        return null;
      })
      .finally(() => { refreshInProgress = null; });
  }
  return refreshInProgress;
}

export async function currentSession(): Promise<AuthSession | null> {
  if (memorySession && memorySession.expires_at > Math.floor(Date.now() / 1000) + 60) return memorySession;
  return refreshSession();
}

export async function signOut() {
  try { await postAuth("sign-out", {}, memorySession?.access_token); }
  catch { /* Always clear local credentials if the service is unavailable. */ }
  memorySession = null;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  let session = await currentSession();
  const send = (accessToken: string | undefined) => {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(input, { ...init, credentials: "include", headers }).catch(() => {
      throw new Error("We couldn't connect to your workspace. Check your connection and try again.");
    }).then(hideTechnicalErrorDetails);
  };
  let response = await send(session?.access_token);
  if (response.status === 401 && session) {
    session = await refreshSession();
    if (session) response = await send(session.access_token);
  }
  return response;
}
