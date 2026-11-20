"use client";

import { RiAddLine, RiCheckLine, RiCloseLine } from "@remixicon/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Chip, Select, SelectItem, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { competitorsQuery } from "@/lib/api/queries";
import type { CompetitorRole, CompetitorView } from "@/lib/api/types";
import { RELATION_LABELS, ROLE_LABELS } from "@/lib/labels";
import { cx } from "@/lib/cx";
import { SettingsSection } from "./section";

const MIN = 3;
const MAX = 10;
const ROLES = ["competitor_set", "niche_set", "benchmark_set"] as const;
const ROLE_HINTS: Record<(typeof ROLES)[number], string> = {
  competitor_set: "Compared with you everywhere",
  niche_set: "Watched for trends and outliers",
  benchmark_set: "Used for format and title ideas only",
};

export function CompetitorsEditor({ clientId, competitors, onChanged }: { clientId: string; competitors: CompetitorView[]; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [handle, setHandle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const tracked = competitors.filter((item) => item.role === "competitor_set" && item.status === "confirmed").length;
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: competitorsQuery(clientId).queryKey });
    onChanged();
  };
  const fail = (caught: unknown) => toast.error(isApiError(caught) ? caught.message : "Couldn't save that. Try again.");

  const change = useMutation({
    mutationFn: async ({ item, action, role }: { item: CompetitorView; action: "confirm" | "reject" | "move"; role?: CompetitorRole }) => {
      if (action === "confirm") return api.competitors.confirm(clientId, item.id);
      if (action === "reject") return api.competitors.reject(clientId, item.id, { reason: "not_competitor" });
      return api.competitors.move(clientId, item.id, role ?? item.role);
    },
    onSuccess: refresh,
    onError: fail,
  });
  const add = useMutation({
    mutationFn: async (ref: string) => api.competitors.add(clientId, { channel_ref: ref, role: "competitor_set" }, crypto.randomUUID()),
    onSuccess: (added) => {
      setHandle("");
      refresh();
      toast.success(`Tracking ${added.name}`);
    },
    onError: fail,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = handle.trim();
    if (!/^(@[\w.-]{2,}|https?:\/\/(www\.)?youtube\.com\/.+|UC[\w-]{22})$/.test(value)) {
      setError("Enter a channel @handle, a youtube.com link or a channel id (UC…).");
      return;
    }
    setError(null);
    add.mutate(value);
  }

  const visible = competitors.filter((item) => item.status !== "rejected");
  const rejected = competitors.filter((item) => item.status === "rejected");

  return (
    <div className="flex flex-col gap-4">
      <SettingsSection
        title="Channels we watch"
        description="Competitors are compared with you everywhere. Niche channels feed trends; benchmarks only inform formats and titles."
        action={
          <span className={cx("rounded-full px-2.5 py-1 text-caption-1-medium tabular-nums", tracked < MIN || tracked > MAX ? "bg-[color-mix(in_oklab,var(--warning)_12%,var(--card))] text-warning" : "bg-muted text-muted-foreground")}>
            {tracked} of {MIN}–{MAX} competitors
          </span>
        }
      >
        <ul className="-mt-1 divide-y divide-border">
          {visible.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
              <div className="min-w-0 flex-1 basis-56">
                <p className="truncate text-body-medium text-foreground">{item.name}</p>
                <p className="truncate text-caption-1-regular text-muted-foreground">
                  {item.channel?.handle ?? "no channel"} · {RELATION_LABELS[item.relation]} · {item.evidence_summary}
                </p>
              </div>
              {item.status === "suggested" ? (
                <Chip variant="caption" color="yellow">
                  Suggested
                </Chip>
              ) : null}
              <Select
                aria-label={`Set for ${item.name}`}
                size="sm"
                selectedKey={item.role}
                onSelectionChange={(key) => key && key !== item.role && change.mutate({ item, action: "move", role: key as CompetitorRole })}
                className="w-40"
              >
                {ROLES.map((role) => (
                  <SelectItem key={role} id={role} textValue={ROLE_LABELS[role]}>
                    <span className="flex flex-col">
                      <span>{ROLE_LABELS[role]}</span>
                      <span className="text-caption-1-regular text-muted-foreground">{ROLE_HINTS[role]}</span>
                    </span>
                  </SelectItem>
                ))}
              </Select>
              {item.status === "suggested" ? (
                <Button variant="secondary" size="xs" leadingIcon={RiCheckLine} onClick={() => change.mutate({ item, action: "confirm" })} disabled={change.isPending}>
                  Track
                </Button>
              ) : null}
              <Button variant="ghost" size="xs" iconOnly leadingIcon={RiCloseLine} aria-label={`Stop watching ${item.name}`} onClick={() => change.mutate({ item, action: "reject" })} disabled={change.isPending} />
            </li>
          ))}
        </ul>
        <form onSubmit={submit} className="flex flex-col gap-1.5">
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="add-competitor">
              Add a channel
            </label>
            <input
              id="add-competitor"
              value={handle}
              onChange={(event) => setHandle(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "add-competitor-error" : undefined}
              placeholder="@channelhandle or youtube.com link"
              className="h-9 min-w-0 flex-1 rounded-control border border-dashed border-border-strong bg-card px-3 text-body-regular outline-none placeholder:text-subtle-foreground focus-visible:border-ring"
            />
            <Button type="submit" variant="secondary" size="xs" leadingIcon={RiAddLine} disabled={add.isPending || !handle.trim()}>
              Add
            </Button>
          </div>
          {error ? (
            <p id="add-competitor-error" role="alert" className="text-caption-1-regular text-danger">
              {error}
            </p>
          ) : null}
        </form>
      </SettingsSection>
      {rejected.length ? (
        <details className="rounded-card bg-card p-4 shadow-card">
          <summary className="cursor-pointer text-body-medium">Not watching · {rejected.length}</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {rejected.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-2 text-body-regular text-muted-foreground">
                {item.name}
                <Button variant="ghost" size="xs" onClick={() => change.mutate({ item, action: "confirm" })}>
                  Watch again
                </Button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
