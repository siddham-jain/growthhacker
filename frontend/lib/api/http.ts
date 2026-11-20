import { API_BASE } from "@/lib/env";
import { getApiKey } from "./credentials";
import { ApiError } from "./errors";
import type { Problem } from "./types";

type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  idempotencyKey?: string;
  ifMatch?: string | number;
  contentType?: string;
}

export interface HttpResponse<T> {
  status: number;
  data: T;
  headers: Headers;
}

export function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${API_BASE}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function toApiError(response: Response): Promise<ApiError> {
  const isProblem = response.headers.get("content-type")?.includes("problem+json");
  if (!isProblem) return new ApiError(response.status, "http_error", `Request failed with ${response.status}`);
  const problem = (await response.json()) as Problem;
  return new ApiError(response.status, problem.code, problem.detail || problem.title, problem);
}

export async function send<T>(method: string, path: string, options: RequestOptions = {}): Promise<HttpResponse<T>> {
  // cookie-authenticated writes need a header a cross-site form cannot set
  const headers = new Headers({ Accept: "application/json", "X-Requested-With": "fetch" });
  const apiKey = getApiKey();
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);
  if (options.body !== undefined) headers.set("Content-Type", options.contentType ?? "application/json");
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  if (options.ifMatch !== undefined) headers.set("If-Match", `"${options.ifMatch}"`);

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "network_error", "We can't reach GrowthHacker right now.");
  }

  if (!response.ok) throw await toApiError(response);
  const data = response.status === 204 ? undefined : await response.json();
  return { status: response.status, data: data as T, headers: response.headers };
}

export async function request<T>(method: string, path: string, options?: RequestOptions): Promise<T> {
  return (await send<T>(method, path, options)).data;
}
