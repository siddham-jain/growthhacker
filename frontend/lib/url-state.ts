"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import type { LensFilter, WindowDays } from "@/lib/api/types";

const WINDOWS: WindowDays[] = [7, 30, 90];
const LENSES: LensFilter[] = ["both", "views", "conversion"];

function useSetParam() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  return useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (value === null) next.delete(key);
      else next.set(key, value);
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
}

export function useViewParams(defaultWindow: WindowDays = 30) {
  const params = useSearchParams();
  const setParam = useSetParam();
  const parsedWindow = Number(params.get("w")) as WindowDays;
  const parsedLens = params.get("lens") as LensFilter;
  return {
    windowDays: WINDOWS.includes(parsedWindow) ? parsedWindow : defaultWindow,
    lens: LENSES.includes(parsedLens) ? parsedLens : "both",
    setWindow: (value: WindowDays) => setParam("w", String(value)),
    setLens: (value: LensFilter) => setParam("lens", value === "both" ? null : value),
  };
}

export function useTabParam<T extends string>(tabs: readonly T[], fallback: T, key = "tab") {
  const params = useSearchParams();
  const setParam = useSetParam();
  const value = params.get(key) as T;
  return [tabs.includes(value) ? value : fallback, (next: T) => setParam(key, next === fallback ? null : next)] as const;
}
