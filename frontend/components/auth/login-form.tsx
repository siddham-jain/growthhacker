"use client";

import { RiArrowRightLine } from "@remixicon/react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { SegmentedControl, SegmentedControlItem } from "@/components/ui/primitives";
import { TextField } from "@/components/ui/text-field";
import { api } from "@/lib/api";
import { setApiKey } from "@/lib/api/credentials";
import { isApiError } from "@/lib/api/errors";
import { meQuery } from "@/lib/api/queries";
import type { Me } from "@/lib/api/types";
import { API_MOCK } from "@/lib/env";

type Method = "session" | "api_key";

// only same-origin paths are honoured, so a crafted link can't bounce the user elsewhere
export function safeNext(next: string | null): string | null {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : null;
}

function landingFor(me: Me): string {
  const workspace = me.workspaces.find((item) => item.id === me.active_workspace_id) ?? me.workspaces[0];
  return `/w/${workspace?.slug ?? "demo"}/clients`;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [method, setMethod] = useState<Method>("session");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const expired = params.get("expired") === "1";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (method === "session" && (!email.includes("@") || !password)) {
      setError("Enter your work email and password.");
      return;
    }
    if (method === "api_key" && key.trim().length < 12) {
      setError("Paste the full workspace API key.");
      return;
    }
    setSubmitting(true);
    try {
      if (method === "session") {
        setApiKey(null);
        await api.auth.login({ email: email.trim(), password });
      } else {
        setApiKey(key);
      }
      queryClient.removeQueries({ queryKey: meQuery().queryKey });
      const me = await queryClient.fetchQuery(meQuery());
      router.replace(safeNext(params.get("next")) ?? landingFor(me));
    } catch (caught) {
      if (method === "api_key") setApiKey(null);
      setError(isApiError(caught) && caught.status === 401 ? (method === "api_key" ? "That API key isn't valid or has been revoked." : "That email and password don't match.") : "We can't reach GrowthHacker right now. Try again.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5" noValidate>
      {expired ? <InfoNote tone="warning">Please sign in again to keep going.</InfoNote> : null}
      <SegmentedControl
        aria-label="Sign-in method"
        selectedKeys={[method]}
        onSelectionChange={(keys) => {
          const [value] = [...keys];
          if (value) setMethod(String(value) as Method);
          setError(null);
        }}
      >
        <SegmentedControlItem id="session">Email</SegmentedControlItem>
        <SegmentedControlItem id="api_key">API key</SegmentedControlItem>
      </SegmentedControl>
      {method === "session" ? (
        <>
          <TextField label="Work email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" />
          <TextField label="Password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} error={error} />
        </>
      ) : (
        <TextField
          label="Workspace API key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          error={error}
          hint="Kept in this tab's memory only. You'll paste it again after a reload."
        />
      )}
      <Button type="submit" variant="primary" size="medium" trailingIcon={RiArrowRightLine} disabled={submitting} className="h-13 rounded-full text-[15px]">
        {submitting ? "Signing in…" : "Sign in"}
      </Button>
      {API_MOCK ? <p className="text-caption-1-regular text-subtle-foreground">Demo data: any email and password work, and any key of 12 characters or more.</p> : null}
    </form>
  );
}
