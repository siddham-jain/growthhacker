"use client";

import { RiArrowUpDownLine, RiPlugLine } from "@remixicon/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode, type TextareaHTMLAttributes } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Chip, Select, SelectItem, Switch, SwitchCard, toast } from "@/components/ui/primitives";
import { TextField } from "@/components/ui/text-field";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { llmSettingsQuery } from "@/lib/api/queries";
import type { LlmProviderId, LlmProviderOut, LlmSettings, LlmSettingsIn, LlmTestOut, LlmTier } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { diffDraft, TIER_IDS, toDraft, type DraftErrors, type TierDraft } from "./llm-draft";
import { SettingsSection } from "./section";

const TIER_LABELS: Record<LlmTier, { label: string; use: string }> = {
  low: { label: "Light", use: "tagging, matching and short checks" },
  medium: { label: "Standard", use: "card wording and refresh lines" },
  high: { label: "Deep", use: "profile reading and hard judgement calls" },
};

const PROVIDERS: Record<LlmProviderId, { name: string; description: string; urlPlaceholder: string }> = {
  anthropic: { name: "Anthropic", description: "Claude models, used for every step that needs wording or reading.", urlPlaceholder: "https://api.anthropic.com" },
  openai_compat: { name: "OpenAI-compatible", description: "Any server that speaks the OpenAI chat API, such as a gateway or a self-hosted model. Only public data is sent to it.", urlPlaceholder: "https://gateway.example.com/v1" },
};

const EFFORTS = [
  { id: "", label: "Default" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
];

const TOKEN_PARAMS = [
  { id: "max_completion_tokens", label: "max_completion_tokens" },
  { id: "max_tokens", label: "max_tokens (older servers)" },
];

const DIALECTS = [
  { id: "openai_json_schema_strict", label: "JSON schema, strict" },
  { id: "openai_json_schema", label: "JSON schema" },
  { id: "openai_json_object", label: "JSON object" },
  { id: "prompt_only", label: "Schema in the prompt" },
];

const CHECK_COLORS: Record<LlmTestOut["checks"][number]["status"], string> = { ok: "bg-success", degraded: "bg-warning", failed: "bg-danger", not_configured: "bg-subtle-foreground" };

function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: LlmSettingsIn) => api.settings.updateLlm(input),
    onSuccess: (next: LlmSettings) => queryClient.setQueryData(llmSettingsQuery().queryKey, next),
    onError: (caught) => toast.error(isApiError(caught) ? caught.message : "Couldn't save. Try again."),
  });
}

function JsonField({ label, hint, error, ...props }: { label: string; hint: string; error?: string } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = `json-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-body-medium text-foreground">
        {label}
      </label>
      <textarea
        id={id}
        rows={3}
        spellCheck={false}
        aria-invalid={Boolean(error)}
        aria-describedby={`${id}-help`}
        className={cx("rounded-input border bg-card px-3 py-2 font-mono text-caption-1-regular text-foreground shadow-raised outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30", error ? "border-danger" : "border-border")}
        placeholder="{}"
        {...props}
      />
      <p id={`${id}-help`} role={error ? "alert" : undefined} className={cx("text-caption-1-regular", error ? "text-danger" : "text-muted-foreground")}>
        {error ?? hint}
      </p>
    </div>
  );
}

function Disclosure({ title, summary, children }: { title: string; summary: string; children: ReactNode }) {
  return (
    <details className="group rounded-control border border-border">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-body-medium text-foreground">
        {title}
        <span className="truncate text-caption-1-regular text-muted-foreground group-open:hidden">{summary}</span>
      </summary>
      <div className="flex flex-col gap-3 border-t border-border p-3">{children}</div>
    </details>
  );
}

function TestResult({ result }: { result: LlmTestOut }) {
  return (
    <ul aria-label="Connection check" className="flex flex-col gap-1 rounded-control bg-muted px-3 py-2">
      {result.checks.map((check, index) => (
        <li key={`${check.model}-${index}`} className="flex flex-wrap items-center gap-2 text-caption-1-regular">
          <span aria-hidden className={cx("size-2 rounded-full", CHECK_COLORS[check.status])} />
          <span className="sr-only">{check.status.replace("_", " ")}</span>
          <span className="text-foreground">{check.model ?? "Provider"}</span>
          <span className="text-muted-foreground">{check.detail}</span>
        </li>
      ))}
    </ul>
  );
}

function ProviderPanel({ id, position, provider, canEdit }: { id: LlmProviderId; position: number; provider: LlmProviderOut; canEdit: boolean }) {
  const [initial] = useState(() => toDraft(provider));
  const [draft, setDraft] = useState(initial);
  const [errors, setErrors] = useState<DraftErrors>({});
  const save = useSaveSettings();
  const test = useMutation({ mutationFn: () => api.settings.testLlm(id), onError: (caught) => toast.error(isApiError(caught) ? caught.message : "Couldn't run the check.") });
  const meta = PROVIDERS[id];
  const isCompat = provider.kind === "openai_compatible";
  const setTier = (tier: LlmTier, patch: Partial<TierDraft>) => setDraft({ ...draft, tiers: { ...draft.tiers, [tier]: { ...draft.tiers[tier], ...patch } } });
  const tierSummary = TIER_IDS.map((tier) => `${TIER_LABELS[tier].label}: ${draft.tiers[tier].model || "none"}`).join(" · ");

  function submit(event: FormEvent) {
    event.preventDefault();
    const { input, errors: found } = diffDraft(initial, draft, provider.kind);
    setErrors(found);
    if (Object.keys(found).length) return;
    if (!Object.keys(input).length) {
      toast("Nothing changed");
      return;
    }
    save.mutate({ providers: { [id]: input } }, { onSuccess: () => toast.success(`${meta.name} saved`, { description: input.api_key ? "The key is stored encrypted on the server and can't be shown again." : undefined }) });
  }

  return (
    <SettingsSection
      title={`${position}. ${meta.name}`}
      description={meta.description}
      action={
        <div className="flex items-center gap-3">
          <Chip variant="caption" color={provider.configured ? "lime" : "soft"}>
            {provider.api_key_set ? (provider.api_key_source === "environment" ? "Key from server config" : "Key saved") : "No key yet"}
          </Chip>
          <Switch
            aria-label={`Use ${meta.name}`}
            isSelected={provider.enabled}
            isDisabled={!canEdit || save.isPending}
            onChange={(enabled) => save.mutate({ providers: { [id]: { enabled } } }, { onSuccess: () => toast.success(`${meta.name} ${enabled ? "on" : "off"}`) })}
          >
            {provider.enabled ? "On" : "Off"}
          </Switch>
        </div>
      }
    >
      <form onSubmit={submit} className={cx("flex flex-col gap-4", !provider.enabled && "opacity-80")} noValidate>
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="Base URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder={meta.urlPlaceholder} spellCheck={false} autoComplete="off" error={errors.base_url} hint={isCompat ? "Usually ends in /v1." : "Leave as is unless you route through a proxy."} />
          <TextField
            label="API key"
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={draft.api_key}
            onChange={(event) => setDraft({ ...draft, api_key: event.target.value, clear_api_key: false })}
            placeholder={provider.api_key_set ? "•••••••• saved · paste a new key to replace it" : "Paste the key"}
            hint="Write-only. Stored encrypted and never shown again."
          />
        </div>
        {provider.api_key_set && provider.api_key_source === "workspace" ? (
          <label className="flex items-center gap-2 text-body-regular text-muted-foreground">
            <input type="checkbox" checked={draft.clear_api_key} onChange={(event) => setDraft({ ...draft, clear_api_key: event.target.checked, api_key: "" })} />
            Remove the saved key{provider.overridden.includes("api_key") && provider.kind === "anthropic" ? " (falls back to the server's key, if any)" : ""}
          </label>
        ) : null}
        <Disclosure title="Models per tier" summary={tierSummary}>
          {TIER_IDS.map((tier) => (
            <div key={tier} className="grid items-end gap-3 sm:grid-cols-[1fr_9rem]">
              <TextField label={`${TIER_LABELS[tier].label} model`} hint={TIER_LABELS[tier].use} value={draft.tiers[tier].model} onChange={(event) => setTier(tier, { model: event.target.value })} placeholder="No model for this tier" spellCheck={false} autoComplete="off" />
              <Select label="Effort" selectedKey={draft.tiers[tier].effort} onSelectionChange={(key) => setTier(tier, { effort: String(key ?? "") as TierDraft["effort"] })} className="mb-6 w-full">
                {EFFORTS.map((effort) => (
                  <SelectItem key={effort.id} id={effort.id} textValue={effort.label}>
                    {effort.label}
                  </SelectItem>
                ))}
              </Select>
            </div>
          ))}
          <p className="text-caption-1-regular text-muted-foreground">Clear a field to go back to the server default. Effort is ignored by models that don&apos;t support it.</p>
        </Disclosure>
        <Disclosure title="Limits and retries" summary={`${draft.tiers.medium.max_tokens} tokens · ${draft.tiers.medium.timeout_s}s · ${draft.tiers.medium.max_retries} retries (Standard)`}>
          {TIER_IDS.map((tier) => (
            <fieldset key={tier} className="grid gap-3 sm:grid-cols-3">
              <legend className="mb-1 text-caption-1-medium text-muted-foreground">{TIER_LABELS[tier].label}</legend>
              <TextField label="Max tokens" inputMode="numeric" value={draft.tiers[tier].max_tokens} onChange={(event) => setTier(tier, { max_tokens: event.target.value })} error={errors[`tiers.${tier}.max_tokens`]} />
              <TextField label="Timeout (seconds)" inputMode="decimal" value={draft.tiers[tier].timeout_s} onChange={(event) => setTier(tier, { timeout_s: event.target.value })} error={errors[`tiers.${tier}.timeout_s`]} />
              <TextField label="Retries" inputMode="numeric" value={draft.tiers[tier].max_retries} onChange={(event) => setTier(tier, { max_retries: event.target.value })} error={errors[`tiers.${tier}.max_retries`]} />
            </fieldset>
          ))}
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Parallel requests" inputMode="numeric" value={draft.max_concurrency} onChange={(event) => setDraft({ ...draft, max_concurrency: event.target.value })} error={errors.max_concurrency} />
            <TextField label="Connect timeout (seconds)" inputMode="decimal" value={draft.connect_timeout_s} onChange={(event) => setDraft({ ...draft, connect_timeout_s: event.target.value })} error={errors.connect_timeout_s} />
          </div>
        </Disclosure>
        {isCompat ? (
          <Disclosure title="Request options" summary={draft.extra_params ? "Extra parameters set" : "Defaults"}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Select label="Token limit parameter" selectedKey={draft.token_param} onSelectionChange={(key) => setDraft({ ...draft, token_param: String(key ?? "") })} className="w-full">
                {TOKEN_PARAMS.map((option) => (
                  <SelectItem key={option.id} id={option.id} textValue={option.label}>
                    {option.label}
                  </SelectItem>
                ))}
              </Select>
              <Select label="Structured output" selectedKey={draft.structured_dialect} onSelectionChange={(key) => setDraft({ ...draft, structured_dialect: String(key ?? "") })} className="w-full">
                {DIALECTS.map((option) => (
                  <SelectItem key={option.id} id={option.id} textValue={option.label}>
                    {option.label}
                  </SelectItem>
                ))}
              </Select>
            </div>
            <label className="flex items-center gap-2 text-body-regular text-foreground">
              <input type="checkbox" checked={draft.supports_reasoning_effort} onChange={(event) => setDraft({ ...draft, supports_reasoning_effort: event.target.checked })} />
              Send each tier&apos;s effort as <code className="font-mono text-caption-1-regular">reasoning_effort</code>
            </label>
            <JsonField label="Extra parameters" hint='Added to every request, e.g. {"temperature": 0.2}.' value={draft.extra_params} onChange={(event) => setDraft({ ...draft, extra_params: event.target.value })} error={errors.extra_params} />
            <JsonField label="Extra body" hint='Gateway-specific fields, e.g. {"provider": {"sort": "price"}}.' value={draft.extra_body} onChange={(event) => setDraft({ ...draft, extra_body: event.target.value })} error={errors.extra_body} />
          </Disclosure>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="small" disabled={!canEdit || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="secondary" size="small" leadingIcon={RiPlugLine} disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? "Checking…" : "Test connection"}
          </Button>
          {provider.overridden.some((field) => field !== "api_key" && field !== "enabled") ? (
            <Button type="button" variant="ghost" size="small" disabled={!canEdit || save.isPending} onClick={() => save.mutate({ providers: { [id]: { reset: true } } }, { onSuccess: () => toast.success(`${meta.name} back to server defaults`) })}>
              Reset to defaults
            </Button>
          ) : null}
        </div>
        {test.data ? <TestResult result={test.data} /> : null}
        <p className="text-caption-1-regular text-subtle-foreground">The check reads the key and model list from the saved settings. It doesn&apos;t send a prompt.</p>
      </form>
    </SettingsSection>
  );
}

// workspace-wide and owner only; key fields are write-only
export function EngineSettings({ settings, canEdit }: { settings: LlmSettings; canEdit: boolean }) {
  const save = useSaveSettings();
  const order = settings.provider_order.length === 2 ? settings.provider_order : (["anthropic", "openai_compat"] as LlmProviderId[]);
  return (
    <div className="flex flex-col gap-4">
      <InfoNote>
        These models write card wording and read websites. They never compute a number: every ratio and count comes from fixed calculations, and wording is checked against the evidence before you see it.
      </InfoNote>
      {canEdit ? null : <InfoNote tone="warning">Only workspace owners can change these settings.</InfoNote>}
      <fieldset disabled={!canEdit} className="flex flex-col gap-4 disabled:opacity-70">
        <SettingsSection
          title="Language models"
          description={`We try ${PROVIDERS[order[0]].name} first and fall back to ${PROVIDERS[order[1]].name} when it's down or out of budget. A provider that's off is skipped.`}
          action={
            <Button variant="secondary" size="small" leadingIcon={RiArrowUpDownLine} disabled={!canEdit || save.isPending} onClick={() => save.mutate({ provider_order: [order[1], order[0]] }, { onSuccess: () => toast.success(`${PROVIDERS[order[1]].name} now goes first`) })}>
              Swap order
            </Button>
          }
        >
          <SwitchCard
            title="Use language models"
            description="Off: no prompt leaves GrowthHacker. Analyses still run and every number stays the same; steps that need a model are skipped."
            isSelected={settings.enabled}
            isDisabled={!canEdit || save.isPending}
            onChange={(enabled) => save.mutate({ enabled }, { onSuccess: () => toast.success(enabled ? "Language models on" : "Language models off") })}
          />
        </SettingsSection>
        {order.map((id, index) => {
          const provider = settings.providers[id];
          return provider ? <ProviderPanel key={`${id}-${settings.updated_at}`} id={id} position={index + 1} provider={provider} canEdit={canEdit} /> : null;
        })}
      </fieldset>
    </div>
  );
}
