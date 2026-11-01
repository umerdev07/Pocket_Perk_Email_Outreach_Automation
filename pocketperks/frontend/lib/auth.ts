export type WorkspaceRole = "admin" | "operator" | "reviewer";

export type AuthSession = {
  access_token: string;
  refresh_token: string;
  expires_at: number;
  user: { id: string; email?: string };
};

const storageKey = "pocketperks.auth.session";
let refreshInProgress: Promise<AuthSession | null> | null = null;

function authConfig() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("Set the Supabase URL and publishable key in frontend/.env.local.");
  return { url, key };
}

function getStoredSession(): AuthSession | null {
  if (typeof window === "undefined") return null;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey) || "null") as AuthSession | null;
    return parsed?.access_token && parsed?.refresh_token ? parsed : null;
  } catch {
    return null;
  }
}

function saveSession(session: AuthSession) {
  window.localStorage.setItem(storageKey, JSON.stringify(session));
}

async function authRequest(path: string, body: Record<string, string>, token?: string) {
  const { url, key } = authConfig();
  let response: Response;
  try {
    response = await fetch(`${url}/auth/v1/${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: key,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Could not connect to Supabase Auth. Check the project URL, internet access, and browser connection.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data.msg === "string" ? data.msg : typeof data.message === "string" ? data.message : "Sign-in failed. Check your email and password.";
    throw new Error(message);
  }
  return data;
}

export async function signIn(email: string, password: string): Promise<AuthSession> {
  const data = await authRequest("token?grant_type=password", { email, password });
  const session: AuthSession = { ...data, expires_at: data.expires_at || Math.floor(Date.now() / 1000) + (data.expires_in || 3600) };
  saveSession(session);
  return session;
}

export async function requestPasswordReset(email: string): Promise<void> {
  const redirectTo = `${window.location.origin}/auth/reset-password`;
  await authRequest(`recover?redirect_to=${encodeURIComponent(redirectTo)}`, { email });
}

export async function setNewPassword(accessToken: string, password: string): Promise<void> {
  const { url, key } = authConfig();
  let response: Response;
  try {
    response = await fetch(`${url}/auth/v1/user`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", apikey: key, Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify({ password }),
    });
  } catch {
    throw new Error("Could not connect to Supabase Auth. Check the project URL and internet connection.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.msg === "string" ? data.msg : typeof data.message === "string" ? data.message : "The recovery link is invalid or expired. Request a new one.");
  }
}

async function refreshSession(existing: AuthSession): Promise<AuthSession | null> {
  if (!refreshInProgress) {
    refreshInProgress = authRequest("token?grant_type=refresh_token", { refresh_token: existing.refresh_token })
      .then(data => {
        const next: AuthSession = { ...data, expires_at: data.expires_at || Math.floor(Date.now() / 1000) + (data.expires_in || 3600) };
        saveSession(next);
        return next;
      })
      .catch(() => {
        window.localStorage.removeItem(storageKey);
        return null;
      })
      .finally(() => { refreshInProgress = null; });
  }
  return refreshInProgress;
}

export async function currentSession(): Promise<AuthSession | null> {
  const session = getStoredSession();
  if (!session) return null;
  if (session.expires_at > Math.floor(Date.now() / 1000) + 60) return session;
  return refreshSession(session);
}

export async function signOut() {
  const session = getStoredSession();
  if (session) {
    try { await authRequest("logout", {}, session.access_token); } catch { /* Clear local access even if sign-out endpoint is offline. */ }
  }
  window.localStorage.removeItem(storageKey);
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  let session = await currentSession();
  const send = (accessToken: string | undefined) => {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(input, { ...init, headers }).catch(() => {
      throw new Error("Could not reach the PocketPerks backend. Check that it is running and NEXT_PUBLIC_API_URL is correct.");
    });
  };
  let response = await send(session?.access_token);
  if (response.status === 401 && session) {
    session = await refreshSession(session);
    if (session) response = await send(session.access_token);
  }
  return response;
}
