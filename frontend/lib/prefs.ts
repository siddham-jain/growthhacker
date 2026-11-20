"use client";

import { useCallback, useSyncExternalStore } from "react";

// ui preferences only (plan 13 §3.2); api data never goes to browser storage
export interface UiPreferences {
  dismissed_hints: string[];
}

const STORAGE_KEY = "gh:prefs:v1";
const EMPTY: UiPreferences = { dismissed_hints: [] };
const listeners = new Set<() => void>();
let cached: UiPreferences | null = null;

function read(): UiPreferences {
  if (cached) return cached;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    cached = raw ? { ...EMPTY, ...(JSON.parse(raw) as Partial<UiPreferences>) } : EMPTY;
  } catch {
    cached = EMPTY;
  }
  return cached;
}

function write(next: UiPreferences) {
  cached = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // storage can be blocked (private mode); the preference then lasts for this page only
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePreferences() {
  const prefs = useSyncExternalStore(subscribe, read, () => EMPTY);
  const dismissHint = useCallback((hint: string) => {
    const current = read();
    if (!current.dismissed_hints.includes(hint)) write({ ...current, dismissed_hints: [...current.dismissed_hints, hint] });
  }, []);
  return { prefs, dismissHint };
}
