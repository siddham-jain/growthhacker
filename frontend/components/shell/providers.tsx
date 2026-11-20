"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Toaster } from "@/components/ui/primitives";
import { isApiError } from "@/lib/api/errors";

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

// an expired session on any request sends the user to sign in and back to where they were
function redirectOnUnauthenticated(error: unknown) {
  if (!isApiError(error) || error.status !== 401 || typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  const next = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
  // a full navigation also drops every cached response from the expired session
  window.location.assign(new URL(`/login?next=${next}&expired=1`, window.location.origin));
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: redirectOnUnauthenticated }),
        mutationCache: new MutationCache({ onError: redirectOnUnauthenticated }),
        defaultOptions: {
          queries: { gcTime: 30 * 60_000, refetchOnWindowFocus: false, retry: shouldRetry },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      {children}
      <Toaster position="bottom-end" />
    </QueryClientProvider>
  );
}
