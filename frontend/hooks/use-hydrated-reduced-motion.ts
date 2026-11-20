"use client";

import { useSyncExternalStore } from "react";
import { usePrefersReducedMotion } from "./use-media-state";

const subscribe = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

/** Keep the first server/client render identical, then apply the user's motion preference. */
export function useHydratedReducedMotion(): boolean {
  const reduced = usePrefersReducedMotion();
  const hydrated = useSyncExternalStore(subscribe, clientSnapshot, serverSnapshot);
  return hydrated && reduced === true;
}
