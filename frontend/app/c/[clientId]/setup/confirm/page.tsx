"use client";

import dynamic from "next/dynamic";
import { LoadingBlock } from "@/components/states/states";

// the step-by-step flow and its motion library load as their own chunk
const ConfirmFlow = dynamic(() => import("@/components/onboarding/confirm-flow").then((module) => module.ConfirmFlow), { ssr: false, loading: () => <LoadingBlock rows={4} /> });

export default function ConfirmPage() {
  return <ConfirmFlow />;
}
