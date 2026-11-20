import { Suspense, type ReactNode } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { LoadingBlock } from "@/components/states/states";

export default function ClientAppLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<LoadingBlock className="p-10" />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
