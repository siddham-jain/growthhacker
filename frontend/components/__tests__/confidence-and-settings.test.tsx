import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { safeNext } from "@/components/auth/login-form";
import { diffDraft, toDraft } from "@/components/settings/llm-draft";
import type { LlmProviderOut } from "@/lib/api/types";
import { ConfidenceMeter } from "@/components/ui/confidence-meter";


describe("ConfidenceMeter", () => {
  it("always prints the label, and First-mover says there are no references", () => {
    render(<ConfidenceMeter confidence={{ label: "Low", outliers: 2, channels: 1 }} />);
    expect(screen.getByText("Low")).toBeInTheDocument();
    render(<ConfidenceMeter confidence={{ label: "Low", outliers: 0, channels: 0 }} firstMover />);
    expect(screen.getByText("First-mover")).toBeInTheDocument();
    expect(screen.getByText(/no reference videos yet/)).toBeInTheDocument();
  });
});

const tier = (model: string | null) => ({ model, max_tokens: 4096, timeout_s: 30, max_retries: 1, effort: null });
const compat: LlmProviderOut = {
  kind: "openai_compatible",
  enabled: true,
  base_url: null,
  tiers: { low: tier(null), medium: tier(null), high: tier(null) },
  max_concurrency: 4,
  connect_timeout_s: 5,
  openai: { extra_params: {}, extra_body: {}, structured_dialect: "openai_json_schema_strict", token_param: "max_completion_tokens", supports_reasoning_effort: false },
  api_key_set: false,
  api_key_source: null,
  configured: false,
  overridden: [],
};

describe("diffDraft", () => {
  it("sends only changed fields and turns blanks into server defaults", () => {
    const initial = toDraft(compat);
    const draft = { ...initial, base_url: "http://localhost:8001/v1", api_key: "sk-local-1234", tiers: { ...initial.tiers, low: { ...initial.tiers.low, model: "qwen", max_tokens: "" } }, extra_params: '{"temperature": 0.2}' };
    const { input, errors } = diffDraft(initial, draft, "openai_compatible");
    expect(errors).toEqual({});
    expect(input).toEqual({ base_url: "http://localhost:8001/v1", api_key: "sk-local-1234", tiers: { low: { model: "qwen", max_tokens: null } }, extra_params: { temperature: 0.2 } });
  });

  it("rejects plain http, out-of-range numbers, bad json and reserved parameters", () => {
    const initial = toDraft(compat);
    const draft = { ...initial, base_url: "http://example.com", max_concurrency: "99", extra_params: "{temperature", extra_body: '{"model": "x"}' };
    const { errors } = diffDraft(initial, draft, "openai_compatible");
    expect(Object.keys(errors).sort()).toEqual(["base_url", "extra_body", "extra_params", "max_concurrency"]);
  });

  it("asks for a base url when a fallback key is pasted without one", () => {
    const initial = toDraft(compat);
    expect(diffDraft(initial, { ...initial, api_key: "sk-local-1234" }, "openai_compatible").errors.base_url).toMatch(/base URL/);
  });
});

describe("safeNext", () => {
  it("only follows same-origin paths after sign-in", () => {
    expect(safeNext("/c/cl/today")).toBe("/c/cl/today");
    expect(safeNext("//evil.com")).toBeNull();
    expect(safeNext("https://evil.com")).toBeNull();
    expect(safeNext(null)).toBeNull();
  });
});
