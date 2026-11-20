"use client";

import { RiAddLine, RiArrowDownSLine, RiCloseLine, RiErrorWarningLine } from "@remixicon/react";
import { useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { cx } from "@/lib/cx";
import { previewItems } from "@/lib/onboarding/edits";

export interface ListItem {
  id: string;
  text: string;
  detail?: string;
  // why this one deserves a second look; flagged rows are listed first by the caller
  check?: string | null;
  source?: { label: string; href?: string; quote?: string; tone: string };
}

interface EditableListProps {
  label: string;
  items: ListItem[];
  noun: string;
  // without it the rows are read-only text (competitors are moved or removed, not renamed)
  onEdit?: (item: ListItem, text: string) => void;
  onRemove: (item: ListItem) => void;
  onRestore: (item: ListItem) => void;
  // resolves to an error message, or null when the item was added
  onAdd: (text: string) => Promise<string | null> | string | null;
  addPlaceholder?: string;
  minLength?: number;
  maxLength?: number;
  empty?: ReactNode;
  // extra controls per row, e.g. moving a competitor between sets
  actions?: (item: ListItem) => ReactNode;
}

interface Removed {
  item: ListItem;
  index: number;
}

function InlineEditor({ item, maxLength, onSave, onCancel }: { item: ListItem; maxLength: number; onSave: (text: string) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(item.text);
  const commit = () => {
    const text = draft.replace(/\s+/g, " ").trim();
    if (text && text !== item.text) onSave(text);
    else onCancel();
  };
  return (
    <input
      aria-label={`Correct “${item.text}”`}
      value={draft}
      maxLength={maxLength}
      autoFocus
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
      className="h-9 min-w-0 flex-1 rounded-control border border-ring bg-background px-2 text-body-regular text-foreground outline-none"
    />
  );
}

function SourceMark({ source }: { source: NonNullable<ListItem["source"]> }) {
  const label = (
    <span className="inline-flex items-center gap-1.5 text-caption-1-regular text-subtle-foreground">
      <span aria-hidden className={cx("size-1.5 shrink-0 rounded-full", source.tone)} />
      <span className="max-w-0 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-200 group-hover:max-w-48 group-hover:opacity-100 group-focus-within:max-w-48 group-focus-within:opacity-100 motion-reduce:transition-none">
        {source.label}
      </span>
    </span>
  );
  return source.href ? (
    <a href={source.href} target="_blank" rel="noreferrer" title={source.quote ? `“${source.quote}”` : source.label} aria-label={`Source: ${source.label}`} className="shrink-0">
      {label}
    </a>
  ) : (
    <span title={source.label} className="shrink-0">
      {label}
    </span>
  );
}

// one calm list per segment: click a line to correct it, × removes it with an undo, the last row adds one
export function EditableList({ label, items, noun, onEdit, onRemove, onRestore, onAdd, addPlaceholder, minLength = 2, maxLength = 200, empty, actions }: EditableListProps) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [removed, setRemoved] = useState<Removed[]>([]);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string[]>([]);
  const [addError, setAddError] = useState<string | null>(null);

  const liveIds = new Set(items.map((item) => item.id));
  const pendingUndo = removed.filter((entry) => !liveIds.has(entry.item.id));
  const { shown, hidden } = previewItems(items, expanded, (item) => added.includes(item.text.toLowerCase()));
  const rows: { item: ListItem; removed: boolean }[] = shown.map((item) => ({ item, removed: false }));
  for (const entry of pendingUndo) rows.splice(Math.min(entry.index, rows.length), 0, { item: entry.item, removed: true });

  function remove(item: ListItem) {
    setRemoved((current) => [...current.filter((entry) => entry.item.id !== item.id), { item, index: items.findIndex((other) => other.id === item.id) }]);
    onRemove(item);
  }

  function restore(item: ListItem) {
    setRemoved((current) => current.filter((entry) => entry.item.id !== item.id));
    onRestore(item);
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const text = draft.replace(/\s+/g, " ").trim();
    if (text.length < minLength) {
      setAddError(`Type at least ${minLength} characters.`);
      return;
    }
    setAdding(true);
    setAddError(null);
    const error = await onAdd(text);
    setAdding(false);
    if (error) setAddError(error);
    else {
      setAdded((current) => [...current, text.toLowerCase()]);
      setDraft("");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {rows.length ? (
        <ul aria-label={label} className="divide-y divide-border overflow-hidden rounded-card bg-card shadow-card">
          {rows.map(({ item, removed: isRemoved }) => (
            <li key={item.id} className={cx("group flex min-h-12 items-center gap-3 px-4 py-2", isRemoved && "bg-muted/60")}>
              {isRemoved ? (
                <>
                  <span className="min-w-0 flex-1 truncate text-body-regular text-subtle-foreground line-through">{item.text}</span>
                  <span className="text-caption-1-regular text-muted-foreground">Removed</span>
                  <button type="button" onClick={() => restore(item)} aria-label={`Undo removing “${item.text}”`} className="rounded-control px-2 py-1 text-caption-1-medium text-foreground underline underline-offset-4 hover:bg-secondary">
                    Undo
                  </button>
                </>
              ) : (
                <>
                  {editing === item.id ? (
                    <InlineEditor
                      item={item}
                      maxLength={maxLength}
                      onCancel={() => setEditing(null)}
                      onSave={(text) => {
                        setEditing(null);
                        onEdit?.(item, text);
                      }}
                    />
                  ) : !onEdit ? (
                    <span className="min-w-0 flex-1 py-1 text-body-regular text-foreground">
                      {item.text}
                      {item.detail ? <span className="mt-0.5 block text-caption-1-regular text-muted-foreground">{item.detail}</span> : null}
                    </span>
                  ) : (
                    <button type="button" onClick={() => setEditing(item.id)} title="Click to correct" aria-label={`Edit “${item.text}”`} className="min-w-0 flex-1 cursor-text rounded-control py-1 text-left text-body-regular text-foreground hover:text-foreground/80">
                      <span className="block decoration-border-strong underline-offset-4 group-hover:underline">{item.text}</span>
                      {item.detail ? <span className="mt-0.5 block text-caption-1-regular text-muted-foreground">{item.detail}</span> : null}
                    </button>
                  )}
                  {item.check ? (
                    <span title={item.check} className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[color-mix(in_oklab,var(--warning)_12%,transparent)] px-2 py-0.5 text-caption-1-medium text-warning">
                      <RiErrorWarningLine aria-hidden className="size-3.5" />
                      Check this
                      <span className="sr-only">: {item.check}</span>
                    </span>
                  ) : null}
                  {item.source ? <SourceMark source={item.source} /> : null}
                  {actions?.(item)}
                  <button
                    type="button"
                    aria-label={`Remove “${item.text}”`}
                    onClick={() => remove(item)}
                    className="grid size-7 shrink-0 place-items-center rounded-control text-subtle-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  >
                    <RiCloseLine aria-hidden className="size-4" />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-card border border-dashed border-border-strong px-4 py-6 text-body-regular text-muted-foreground">{empty ?? `Nothing here yet. Add a ${noun} below.`}</div>
      )}
      {hidden ? (
        <button type="button" onClick={() => setExpanded(true)} className="inline-flex items-center gap-1 self-start rounded-control px-1 text-body-medium text-foreground hover:underline">
          Show all {items.length}
          <RiArrowDownSLine aria-hidden className="size-4" />
        </button>
      ) : null}
      <form onSubmit={add} className="flex flex-col gap-1">
        <div className="flex items-center gap-2 rounded-card border border-dashed border-border-strong bg-transparent px-3 focus-within:border-ring">
          <RiAddLine aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          <input
            aria-label={`Add a ${noun}`}
            value={draft}
            maxLength={maxLength}
            onChange={(event) => {
              setDraft(event.target.value);
              setAddError(null);
            }}
            placeholder={addPlaceholder ?? `Add a ${noun}`}
            className="h-11 min-w-0 flex-1 bg-transparent text-body-regular outline-none placeholder:text-subtle-foreground"
          />
          {draft.trim() ? (
            <button type="submit" disabled={adding} className="shrink-0 rounded-control px-2 py-1 text-caption-1-medium text-foreground hover:bg-secondary disabled:opacity-60">
              {adding ? "Adding…" : "Add ↵"}
            </button>
          ) : null}
        </div>
        {addError ? (
          <p role="alert" className="px-1 text-caption-1-regular text-danger">
            {addError}
          </p>
        ) : null}
      </form>
    </div>
  );
}
