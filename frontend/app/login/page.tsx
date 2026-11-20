import { Suspense } from "react";
import { LoginForm } from "@/components/auth/login-form";
import { Brand } from "@/components/shell/sidebar";
import { LoadingBlock } from "@/components/states/states";
import { AnalysisOrb } from "@/components/ui/effects";
import { Eyebrow } from "@/components/ui/surface";

export const metadata = { title: "Sign in · GrowthHacker" };

const PROOF = [
  "A brief of 3–5 actions each morning, each backed by videos you can check.",
  "Competitor evidence sheets with raw numbers side by side. No scores, no leaderboards.",
  "Every ratio states its age and method, so a proxy never passes for a measurement.",
];

export default function LoginPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="px-4 py-4 sm:px-8">
        <Brand />
      </header>
      <main className="mx-auto grid w-full max-w-[1120px] flex-1 items-start gap-10 px-4 pt-6 pb-16 sm:px-8 lg:grid-cols-[minmax(320px,440px)_1fr] lg:gap-16 lg:pt-12">
        <div className="flex flex-col gap-5">
          <Eyebrow dot>Sign in</Eyebrow>
          <h1 className="text-display text-foreground">Welcome back.</h1>
          <p className="text-body-regular text-muted-foreground">Use your work email, or a workspace API key for scripts and shared screens.</p>
          <Suspense fallback={<LoadingBlock rows={2} />}>
            <LoginForm />
          </Suspense>
        </div>
        <div className="hidden rounded-hero p-4 shadow-hero sm:p-6 lg:block" style={{ background: "var(--hero-surface)" }}>
          <div className="flex flex-col gap-6 rounded-panel bg-card/95 p-6 backdrop-blur">
            <div className="flex items-center gap-3">
              <AnalysisOrb state="breathing" label="GrowthHacker" />
              <p className="text-body-medium text-foreground">What&apos;s waiting for you</p>
            </div>
            <ul className="flex flex-col gap-4">
              {PROOF.map((line) => (
                <li key={line} className="flex gap-3 text-body-regular text-foreground">
                  <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-ring" />
                  {line}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </main>
    </div>
  );
}
