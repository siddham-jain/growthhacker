"use client";

import { useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void) {
  const query = window.matchMedia(REDUCED);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

// the server renders full motion; the client switches after hydration
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReduced, () => window.matchMedia(REDUCED).matches, () => false);
}

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

// follows the `dark` class the theme toggle sets on <html>, without loading the toggle itself
export function useThemeClass(): "light" | "dark" {
  return useSyncExternalStore(subscribeTheme, () => (document.documentElement.classList.contains("dark") ? "dark" : "light"), () => "light");
}
