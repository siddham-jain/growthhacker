import type { LlmProviderIn, LlmProviderOut, LlmTier } from "@/lib/api/types";

export const TIER_IDS: LlmTier[] = ["low", "medium", "high"];

type Effort = "" | "low" | "medium" | "high";

export interface TierDraft {
  model: string;
  effort: Effort;
  max_tokens: string;
  timeout_s: string;
  max_retries: string;
}

// every field is a string while editing; blank means "use the server default"
export interface ProviderDraft {
  base_url: string;
  api_key: string;
  clear_api_key: boolean;
  tiers: Record<LlmTier, TierDraft>;
  max_concurrency: string;
  connect_timeout_s: string;
  extra_params: string;
  extra_body: string;
  token_param: string;
  structured_dialect: string;
  supports_reasoning_effort: boolean;
}

export type DraftErrors = Record<string, string>;

const NUMBER_RULES = {
  max_tokens: { min: 256, max: 64_000, integer: true, label: "Max tokens" },
  timeout_s: { min: 5, max: 600, integer: false, label: "Timeout" },
  max_retries: { min: 0, max: 5, integer: true, label: "Retries" },
  max_concurrency: { min: 1, max: 32, integer: true, label: "Parallel requests" },
  connect_timeout_s: { min: 1, max: 60, integer: false, label: "Connect timeout" },
} as const;

const RESERVED = ["model", "messages", "system", "stream", "max_tokens", "max_completion_tokens", "response_format", "tools"];

const json = (value: Record<string, unknown>) => (Object.keys(value).length ? JSON.stringify(value, null, 2) : "");

export function toDraft(provider: LlmProviderOut): ProviderDraft {
  const tier = (id: LlmTier): TierDraft => {
    const values = provider.tiers[id];
    return { model: values.model ?? "", effort: values.effort ?? "", max_tokens: String(values.max_tokens), timeout_s: String(values.timeout_s), max_retries: String(values.max_retries) };
  };
  return {
    base_url: provider.base_url ?? "",
    api_key: "",
    clear_api_key: false,
    tiers: { low: tier("low"), medium: tier("medium"), high: tier("high") },
    max_concurrency: String(provider.max_concurrency),
    connect_timeout_s: String(provider.connect_timeout_s),
    extra_params: json(provider.openai?.extra_params ?? {}),
    extra_body: json(provider.openai?.extra_body ?? {}),
    token_param: provider.openai?.token_param ?? "",
    structured_dialect: provider.openai?.structured_dialect ?? "",
    supports_reasoning_effort: provider.openai?.supports_reasoning_effort ?? false,
  };
}

function parseNumber(raw: string, rule: (typeof NUMBER_RULES)[keyof typeof NUMBER_RULES], path: string, errors: DraftErrors): number | null {
  const text = raw.trim();
  if (!text) return null;
  const value = Number(text);
  if (!Number.isFinite(value) || (rule.integer && !Number.isInteger(value)) || value < rule.min || value > rule.max) {
    errors[path] = `${rule.label} must be ${rule.integer ? "a whole number " : ""}between ${rule.min} and ${rule.max}.`;
    return null;
  }
  return value;
}

function parseObject(raw: string, path: string, errors: DraftErrors): Record<string, unknown> | null {
  const text = raw.trim();
  if (!text) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not an object");
    const reserved = RESERVED.filter((key) => key in value);
    if (reserved.length) {
      errors[path] = `GrowthHacker sets ${reserved.join(", ")} itself.`;
      return null;
    }
    return value as Record<string, unknown>;
  } catch {
    errors[path] = 'Enter a JSON object, like {"temperature": 0.2}.';
    return null;
  }
}

export function validBaseUrl(value: string): boolean {
  return /^https:\/\/\S+$|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/\S*)?$/.test(value.trim());
}

// only fields the user changed are sent, so untouched values keep following the server defaults
export function diffDraft(initial: ProviderDraft, draft: ProviderDraft, kind: LlmProviderOut["kind"]): { input: LlmProviderIn; errors: DraftErrors } {
  const errors: DraftErrors = {};
  const input: LlmProviderIn = {};
  const base = draft.base_url.trim();
  if (base !== initial.base_url.trim()) {
    if (base && !validBaseUrl(base)) errors.base_url = "Use https://…, or http://localhost for a local server.";
    input.base_url = base || null;
  }
  if (kind === "openai_compatible" && !base && draft.api_key.trim()) errors.base_url = "Add the base URL for this key.";
  if (draft.api_key.trim()) input.api_key = draft.api_key.trim();
  if (draft.clear_api_key) input.clear_api_key = true;

  const tiers: NonNullable<LlmProviderIn["tiers"]> = {};
  for (const id of TIER_IDS) {
    const before = initial.tiers[id];
    const after = draft.tiers[id];
    const change: NonNullable<NonNullable<LlmProviderIn["tiers"]>[LlmTier]> = {};
    if (after.model.trim() !== before.model.trim()) change.model = after.model.trim() || null;
    if (after.effort !== before.effort) change.effort = after.effort || null;
    for (const key of ["max_tokens", "timeout_s", "max_retries"] as const) {
      if (after[key].trim() !== before[key].trim()) change[key] = parseNumber(after[key], NUMBER_RULES[key], `tiers.${id}.${key}`, errors);
    }
    if (Object.keys(change).length) tiers[id] = change;
  }
  if (Object.keys(tiers).length) input.tiers = tiers;

  for (const key of ["max_concurrency", "connect_timeout_s"] as const) {
    if (draft[key].trim() !== initial[key].trim()) input[key] = parseNumber(draft[key], NUMBER_RULES[key], key, errors);
  }
  if (kind === "openai_compatible") {
    for (const key of ["extra_params", "extra_body"] as const) {
      if (draft[key].trim() !== initial[key].trim()) input[key] = parseObject(draft[key], key, errors);
    }
    if (draft.token_param !== initial.token_param) input.token_param = (draft.token_param || null) as LlmProviderIn["token_param"];
    if (draft.structured_dialect !== initial.structured_dialect) input.structured_dialect = (draft.structured_dialect || null) as LlmProviderIn["structured_dialect"];
    if (draft.supports_reasoning_effort !== initial.supports_reasoning_effort) input.supports_reasoning_effort = draft.supports_reasoning_effort;
  }
  return { input, errors };
}
