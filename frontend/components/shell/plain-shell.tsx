import { Suspense, type ReactNode } from "react";
import { LoadingBlock } from "@/components/states/states";
import { AccountMenu, ThemeToggle } from "./lazy-chrome";
import { Brand } from "./sidebar";

// chrome for workspace and onboarding screens, which have no client navigation yet
export function PlainShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between px-4 py-4 sm:px-8">
        <Brand />
        <div className="flex items-center gap-2">
          <AccountMenu />
          <ThemeToggle appearance="segmented" size="small" />
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1280px] flex-1 px-4 pt-4 pb-16 sm:px-8 lg:pt-8">
        <Suspense fallback={<LoadingBlock />}>{children}</Suspense>
      </main>
    </div>
  );
}
