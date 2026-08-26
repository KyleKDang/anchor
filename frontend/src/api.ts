/** The JSON API client: same-origin, cookie-carried session, typed errors. */

export interface Account {
  id: string;
  email: string;
  verified: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw toApiError(response.status, payload);
  return payload as T;
}

function toApiError(status: number, payload: unknown): ApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    return new ApiError(status, String(payload.error.code), String(payload.error.message));
  }
  if (status === 422 && isRecord(payload) && Array.isArray(payload.detail)) {
    const [first] = payload.detail as { loc?: unknown[]; msg?: string }[];
    const field = first?.loc?.at(-1);
    const detail = first?.msg?.replace(/^Value error, /, "") ?? "is not valid";
    return new ApiError(status, "invalid_input", `${capitalize(String(field ?? "Input"))} ${detail}.`);
  }
  return new ApiError(status, "unexpected", `Something went wrong (HTTP ${status}).`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const api = {
  me: () => request<Account>("GET", "/api/auth/me"),
  signUp: (email: string, password: string) =>
    request<Account>("POST", "/api/auth/signup", { email, password }),
  verify: (token: string) => request<Account>("POST", "/api/auth/verify", { token }),
  logIn: (email: string, password: string) =>
    request<Account>("POST", "/api/auth/login", { email, password }),
  logOut: () => request<void>("POST", "/api/auth/logout"),
  deleteAccount: (password: string) => request<void>("DELETE", "/api/account", { password }),
};
