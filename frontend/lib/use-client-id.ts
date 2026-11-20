"use client";

import { useParams } from "next/navigation";

export function useClientId(): string {
  return useParams<{ clientId: string }>().clientId;
}
