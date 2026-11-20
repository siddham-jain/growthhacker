"use client";

import { RiAddLine, RiCheckLine, RiCloseLine, RiPencilLine } from "@remixicon/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { SourcesUsed } from "@/components/onboarding/sources-used";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { SourceKindChip } from "@/components/ui/chips";
import { Chip, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { profileQuery } from "@/lib/api/queries";
import type { BusinessProfileView, FactEdit, ProfileFact } from "@/lib/api/types";
import { newItemId } from "@/lib/onboarding/edits";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import { SettingsSection } from "./section";

const GROUPS = [
  { key: "offerings", label: "Offerings", noun: "offering" },
  { key: "locations", label: "Locations", noun: "location" },
  { key: "conversion_actions", label: "Ways customers reach you", noun: "way to reach you" },
  { key: "value_propositions", label: "Why customers choose you", noun: "reason" },
] as const;

function FactRow({ fact, onSave, onRemove, onConfirm, busy }: { fact: ProfileFact; onSave: (label: string) => void; onRemove: () => void; onConfirm: () => void; busy: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(fact.label);
  const evidence = fact.meta.evidence[0];
  const needsConfirmation = fact.meta.status === "needs_confirmation";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    onSave(draft.trim());
    setEditing(false);
  }

  if (editing) {
    return (
      <li className="py-2">
        <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor={`edit-${fact.id}`}>
            Edit {fact.label}
          </label>
          <input id={`edit-${fact.id}`} value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus className="h-9 min-w-0 flex-1 rounded-control border border-border bg-card px-3 text-body-regular outline-none focus-visible:border-ring" />
          <Button type="submit" variant="primary" size="xs" disabled={busy}>
            Save
          </Button>
          <Button variant="ghost" size="xs" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </form>
      </li>
    );
  }

  return (
    <li className="group flex flex-wrap items-center gap-2 py-2">
      {evidence?.url ? (
        <a href={evidence.url} target="_blank" rel="noreferrer" title={`“${evidence.quote}”`} className="text-body-regular text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground">
          {fact.label}
        </a>
      ) : (
        <span className="text-body-regular text-foreground">{fact.label}</span>
      )}
      {evidence ? <SourceKindChip kind={evidence.source_kind} /> : null}
      {fact.meta.status === "edited" ? (
        <Chip variant="caption" color="soft">
          Edited by you
        </Chip>
      ) : null}
      {needsConfirmation ? (
        <Chip variant="caption" color="yellow" title={evidence ? SOURCE_KIND_LABELS[evidence.source_kind].description : undefined}>
          Please confirm
        </Chip>
      ) : null}
      <span className="ml-auto flex gap-1">
        {needsConfirmation ? (
          <Button variant="secondary" size="xs" leadingIcon={RiCheckLine} onClick={onConfirm} disabled={busy}>
            Confirm
          </Button>
        ) : null}
        <Button variant="ghost" size="xs" iconOnly leadingIcon={RiPencilLine} aria-label={`Edit ${fact.label}`} onClick={() => setEditing(true)} />
        <Button variant="ghost" size="xs" iconOnly leadingIcon={RiCloseLine} aria-label={`Remove ${fact.label}`} onClick={onRemove} disabled={busy} />
      </span>
    </li>
  );
}

function AddFact({ noun, onAdd, busy }: { noun: string; onAdd: (label: string) => void; busy: boolean }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="flex gap-2 pt-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!value.trim()) return;
        onAdd(value.trim());
        setValue("");
      }}
    >
      <label className="sr-only" htmlFor={`add-${noun}`}>
        Add a {noun}
      </label>
      <input id={`add-${noun}`} value={value} onChange={(event) => setValue(event.target.value)} placeholder={`Add a ${noun}`} className="h-9 min-w-0 flex-1 rounded-control border border-dashed border-border-strong bg-card px-3 text-body-regular outline-none placeholder:text-subtle-foreground focus-visible:border-ring" />
      <Button type="submit" variant="secondary" size="xs" leadingIcon={RiAddLine} disabled={busy || !value.trim()}>
        Add
      </Button>
    </form>
  );
}

// edits are written as overrides with the profile version, so a concurrent change is caught instead of overwritten
export function ProfileEditor({ clientId, view, onChanged }: { clientId: string; view: BusinessProfileView; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: async (edit: FactEdit) => api.profile.update(clientId, { facts: [edit] }, view.version),
    onSuccess: (next) => {
      queryClient.setQueryData(profileQuery(clientId).queryKey, next);
      onChanged();
      toast.success("Profile saved");
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 412) {
        queryClient.invalidateQueries({ queryKey: profileQuery(clientId).queryKey });
        toast.error("Someone else changed this", { description: "We loaded the latest version. Re-apply your edit." });
      } else toast.error("Couldn't save. Try again.");
    },
  });
  const profile = view.effective;

  return (
    <div className="flex flex-col gap-4">
      <SettingsSection title="Your business" description={profile.identity.one_liner}>
        <SourcesUsed sources={view.sources} />
        {view.partial ? <InfoNote tone="warning">Your site limits automated reading, so some facts came from other public sources. Confirm or fix anything marked “Please confirm”.</InfoNote> : null}
      </SettingsSection>
      <div className="grid gap-4 lg:grid-cols-2">
        {GROUPS.map((group) => {
          const facts = profile[group.key];
          return (
            <SettingsSection key={group.key} title={`${group.label} · ${facts.length}`}>
              <ul className="-mt-2 divide-y divide-border">
                {facts.map((fact) => (
                  <FactRow
                    key={fact.id}
                    fact={fact}
                    busy={save.isPending}
                    onSave={(label) => save.mutate({ group: group.key, op: "edit", id: fact.id, label })}
                    onRemove={() => save.mutate({ group: group.key, op: "remove", id: fact.id })}
                    onConfirm={() => save.mutate({ group: group.key, op: "confirm", id: fact.id })}
                  />
                ))}
              </ul>
              <AddFact noun={group.noun} busy={save.isPending} onAdd={(label) => save.mutate({ group: group.key, op: "add", id: newItemId(), label })} />
            </SettingsSection>
          );
        })}
      </div>
    </div>
  );
}
