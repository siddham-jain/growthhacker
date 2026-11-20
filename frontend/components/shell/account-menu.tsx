"use client";

import { RiArrowUpDownLine, RiCheckLine, RiLogoutBoxRLine } from "@remixicon/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Popover, PopoverTrigger, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { setApiKey } from "@/lib/api/credentials";
import { meQuery } from "@/lib/api/queries";
import type { MeWorkspace } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { DEMO_WORKSPACE } from "@/lib/env";

const ROLE_LABELS: Record<MeWorkspace["role"], string> = { owner: "Owner", editor: "Editor", viewer: "Viewer" };

export function useActiveWorkspace() {
  const me = useQuery(meQuery());
  const workspace = me.data?.workspaces.find((item) => item.id === me.data?.active_workspace_id) ?? me.data?.workspaces[0];
  return { me: me.data, workspace };
}

export function useWorkspaceSlug(): string {
  return useActiveWorkspace().workspace?.slug ?? DEMO_WORKSPACE;
}

// workspace switcher and sign-out in one place; the switch list only appears with more than one workspace
export function AccountMenu({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { me, workspace } = useActiveWorkspace();
  const [open, setOpen] = useState(false);
  if (!me) return null;
  const who = me.user?.display_name ?? me.user?.email ?? "API key";

  async function switchTo(target: MeWorkspace) {
    setOpen(false);
    if (target.id === workspace?.id) return;
    try {
      await api.auth.switchWorkspace(target.id);
      queryClient.clear();
      router.push(`/w/${target.slug}/clients`);
      toast.success(`Switched to ${target.name}`);
    } catch {
      toast.error("Couldn't switch workspace. Try again.");
    }
  }

  async function signOut() {
    setOpen(false);
    try {
      await api.auth.logout();
    } finally {
      setApiKey(null);
      queryClient.clear();
      router.replace("/login");
    }
  }

  return (
    <PopoverTrigger isOpen={open} onOpenChange={setOpen}>
      <button
        type="button"
        className={cx(
          "flex min-w-0 items-center gap-2.5 rounded-control border border-border bg-card px-2.5 py-1.5 text-left shadow-xs transition-colors hover:border-border-strong",
          compact ? "w-full lg:max-xl:w-auto lg:max-xl:px-1.5" : "max-w-56",
        )}
      >
        <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-secondary text-caption-1-semibold text-foreground">
          {(workspace?.name ?? "W").charAt(0)}
        </span>
        <span className={cx("flex min-w-0 flex-1 flex-col", compact && "lg:max-xl:sr-only")}>
          <span className="truncate text-caption-1-medium text-foreground">{workspace?.name ?? "Workspace"}</span>
          <span className="truncate text-caption-2-medium text-muted-foreground">{who}</span>
        </span>
        <RiArrowUpDownLine aria-hidden className={cx("size-4 shrink-0 text-subtle-foreground", compact && "lg:max-xl:hidden")} />
      </button>
      <Popover placement="bottom start" aria-label="Account" className="w-72">
        {me.workspaces.length > 1 ? (
          <div className="flex flex-col gap-0.5">
            <p className="px-2 pb-1 text-eyebrow text-muted-foreground">Workspaces</p>
            {me.workspaces.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => switchTo(item)}
                className="flex items-center gap-2 rounded-control px-2 py-1.5 text-left text-body-regular text-foreground hover:bg-muted"
              >
                <span className="min-w-0 flex-1 truncate">{item.name}</span>
                <span className="text-caption-1-regular text-muted-foreground">{ROLE_LABELS[item.role]}</span>
                {item.id === workspace?.id ? <RiCheckLine aria-label="Current" className="size-4 text-info" /> : <span className="size-4" />}
              </button>
            ))}
          </div>
        ) : null}
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <p className="px-2 text-caption-1-regular text-muted-foreground">
            {me.principal === "api_key" ? "Signed in with an API key" : `Signed in as ${me.user?.email}`} · {ROLE_LABELS[me.role]}
          </p>
          <button type="button" onClick={signOut} className="flex items-center gap-2 rounded-control px-2 py-1.5 text-left text-body-regular text-foreground hover:bg-muted">
            <RiLogoutBoxRLine aria-hidden className="size-4 text-muted-foreground" />
            Sign out
          </button>
        </div>
      </Popover>
    </PopoverTrigger>
  );
}
