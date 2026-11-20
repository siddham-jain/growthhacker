"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Chip, SwitchCard, toast } from "@/components/ui/primitives";
import { TextField } from "@/components/ui/text-field";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { llmSettingsQuery } from "@/lib/api/queries";
import type { LlmProviderIn, LlmProviderOut, LlmSettings, LlmTier } from "@/lib/api/types";
import { SettingsSection } from "./section";

const TIERS: { id: LlmTier; label: string; use: string }[] = [
  { id: "low", label: "Light", use: "tagging, matching and short checks" },
  { id: "medium", label: "Standard", use: "card wording and refresh lines" },
  { id: "high", label: "Deep", use: "profile reading and hard judgement calls" },
];

const PRIMARY = "anthropic";
const FALLBACK = "openai_compat";

interface ProviderDraft {
  base_url: string;
  api_key: string;
  clear_api_key: boolean;
  models: Record<LlmTier, string>;
}

function toDraft(provider: LlmProviderOut): ProviderDraft {
  return {
    base_url: provider.base_url ?? "",
    api_key: "",
    clear_api_key: false,
    models: { low: provider.models.low ?? "", medium: provider.models.medium ?? "", high: provider.models.high ?? "" },
  };
}

export function validateProvider(draft: Pick<ProviderDraft, "base_url">, kind: LlmProviderOut["kind"], enabled: boolean): string | null {
  if (kind !== "openai_compatible" || !enabled) return null;
  return /^https:\/\/\S+$|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/\S*)?$/.test(draft.base_url.trim()) ? null : "Enter the fallback's base URL (https://…, or http://localhost for a local server).";
}

function toInput(draft: ProviderDraft, kind: LlmProviderOut["kind"]): LlmProviderIn {
  return {
    base_url: kind === "anthropic" ? undefined : draft.base_url.trim() || null,
    api_key: draft.api_key.trim() || undefined,
    clear_api_key: draft.clear_api_key,
    models: { low: draft.models.low.trim() || null, medium: draft.models.medium.trim() || null, high: draft.models.high.trim() || null },
  };
}

function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.settings.updateLlm,
    onSuccess: (next: LlmSettings) => queryClient.setQueryData(llmSettingsQuery().queryKey, next),
    onError: (caught) => toast.error(isApiError(caught) ? caught.message : "Couldn't save. Try again."),
  });
}

function ProviderForm({ id, title, description, provider, fallbackEnabled }: { id: string; title: string; description: string; provider: LlmProviderOut; fallbackEnabled: boolean }) {
  const [draft, setDraft] = useState(() => toDraft(provider));
  const [error, setError] = useState<string | null>(null);
  const save = useSaveSettings();
  const isFallback = provider.kind === "openai_compatible";

  function submit(event: FormEvent) {
    event.preventDefault();
    const problem = validateProvider(draft, provider.kind, fallbackEnabled);
    setError(problem);
    if (problem) return;
    save.mutate(
      { providers: { [id]: toInput(draft, provider.kind) } },
      { onSuccess: () => toast.success(`${title} saved`, { description: draft.api_key ? "The key is stored on the server and can't be shown again." : undefined }) },
    );
  }

  return (
    <SettingsSection
      title={title}
      description={description}
      action={
        <Chip variant="caption" color={provider.configured ? "lime" : "soft"}>
          {provider.api_key_set ? (provider.api_key_source === "environment" ? "Key from server config" : "Key saved") : "No key yet"}
        </Chip>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {isFallback ? (
          <TextField label="Base URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://gateway.example.com/v1" spellCheck={false} autoComplete="off" error={error} />
        ) : (
          <TextField label="Base URL" value={draft.base_url} readOnly hint="Fixed for this provider." />
        )}
        <TextField
          label="API key"
          type="password"
          autoComplete="new-password"
          spellCheck={false}
          value={draft.api_key}
          onChange={(event) => setDraft({ ...draft, api_key: event.target.value, clear_api_key: false })}
          placeholder={provider.api_key_set ? "•••••••• saved · paste a new key to replace it" : "Paste the key"}
          hint="Write-only. We store it on the server and never show it again."
        />
        {provider.api_key_set && provider.api_key_source === "workspace" ? (
          <label className="flex items-center gap-2 text-body-regular text-muted-foreground">
            <input type="checkbox" checked={draft.clear_api_key} onChange={(event) => setDraft({ ...draft, clear_api_key: event.target.checked, api_key: "" })} />
            Remove the saved key
          </label>
        ) : null}
        <details className="group rounded-control border border-border">
          <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-body-medium text-foreground">
            Model per tier
            <span className="text-caption-1-regular text-muted-foreground group-open:hidden">{TIERS.map((tier) => `${tier.label}: ${draft.models[tier.id] || "default"}`).join(" · ")}</span>
          </summary>
          <fieldset className="grid gap-3 border-t border-border p-3 sm:grid-cols-3">
            <legend className="sr-only">Model per tier</legend>
            {TIERS.map((tier) => (
              <TextField
                key={tier.id}
                label={`${tier.label} model`}
                hint={tier.use}
                value={draft.models[tier.id]}
                onChange={(event) => setDraft({ ...draft, models: { ...draft.models, [tier.id]: event.target.value } })}
                placeholder="Server default"
                spellCheck={false}
                autoComplete="off"
              />
            ))}
            <p className="text-caption-1-regular text-muted-foreground sm:col-span-3">Token limits and timeouts per tier are set in the server configuration.</p>
          </fieldset>
        </details>
        <Button type="submit" variant="primary" size="small" className="w-fit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </form>
    </SettingsSection>
  );
}

// workspace-wide and owner only; key fields are write-only
export function EngineSettings({ settings, canEdit }: { settings: LlmSettings; canEdit: boolean }) {
  const save = useSaveSettings();
  const primary = settings.providers[PRIMARY];
  const fallback = settings.providers[FALLBACK];
  const fallbackEnabled = settings.provider_order.includes(FALLBACK);
  return (
    <div className="flex flex-col gap-4">
      <InfoNote>
        These models write card wording and read websites. They never compute a number: every ratio and count comes from fixed calculations, and wording is checked against the evidence before you see it.
      </InfoNote>
      {canEdit ? null : <InfoNote tone="warning">Only workspace owners can change these settings.</InfoNote>}
      <fieldset disabled={!canEdit} className="flex flex-col gap-4 disabled:opacity-70">
        {primary ? <ProviderForm key={`primary-${settings.updated_at}`} id={PRIMARY} title="Primary provider · Anthropic" description="Used for every analysis step that needs wording or reading." provider={primary} fallbackEnabled={false} /> : null}
        <SwitchCard
          title="Use the OpenAI-compatible fallback when the primary is down"
          description="Only for work on public data. Private analytics and comments are never sent to it."
          isSelected={fallbackEnabled}
          isDisabled={!canEdit || save.isPending}
          onChange={(enabled) => save.mutate({ provider_order: enabled ? [PRIMARY, FALLBACK] : [PRIMARY], providers: {} }, { onSuccess: () => toast.success(enabled ? "Fallback on" : "Fallback off") })}
        />
        {fallback && fallbackEnabled ? (
          <ProviderForm
            key={`fallback-${settings.updated_at}`}
            id={FALLBACK}
            title="Fallback · OpenAI-compatible"
            description="Any server that speaks the OpenAI chat API, such as a gateway or a self-hosted model."
            provider={fallback}
            fallbackEnabled
          />
        ) : null}
      </fieldset>
    </div>
  );
}
