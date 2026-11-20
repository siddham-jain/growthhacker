"use client";

import { RiArrowUpDownLine, RiCheckLine, RiLogoutBoxRLine } from "@remixicon/react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Popover, PopoverTrigger, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { setApiKey } from "@/lib/api/credentials";
import type { MeWorkspace } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { useActiveWorkspace } from "./workspace";

const ROLE_LABELS: Record<MeWorkspace["role"], string> = { owner: "Owner", editor: "Editor", viewer: "Viewer" };

function displayName(me: { user?: { display_name?: string | null } | null }, workspaceName?: string): string {
  return me.user?.display_name || workspaceName || "Workspace";
}

function initial(text: string): string {
  return (text.trim().charAt(0) || "W").toUpperCase();
}

// workspace switcher and sign-out in one place; the switch list only appears with more than one workspace
// loaded lazily (it needs react-aria), and it renders nothing until the account has loaded anyway
export default function AccountMenu({ compact = false }: { compact?: boolean }) {
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
          "group flex min-w-0 items-center gap-3 rounded-panel border border-transparent px-2 py-2 text-left transition-colors hover:border-border hover:bg-card",
          compact ? "w-full lg:max-xl:w-auto lg:max-xl:px-1" : "max-w-60",
        )}
      >
        <span
          aria-hidden
          className="grid size-9 shrink-0 place-items-center rounded-full border border-border bg-card font-display text-[19px] leading-none text-foreground shadow-xs"
        >
          {initial(who)}
        </span>
        <span className={cx("flex min-w-0 flex-1 flex-col gap-0.5", compact && "lg:max-xl:sr-only")}>
          <span className="truncate font-display text-[17px] leading-tight text-foreground">{displayName(me, workspace?.name)}</span>
          <span className="truncate font-mono text-[11px] leading-4 text-muted-foreground">{me.user?.email ?? "API key"}</span>
        </span>
        <RiArrowUpDownLine
          aria-hidden
          className={cx("size-3.5 shrink-0 text-subtle-foreground transition-colors group-hover:text-foreground", compact && "lg:max-xl:hidden")}
        />
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
          <div className="flex flex-col gap-1 px-2 pb-1">
            <p className="text-eyebrow text-muted-foreground">{me.principal === "api_key" ? "API key" : "Signed in"} · {ROLE_LABELS[me.role]}</p>
            <p className="truncate font-display text-[19px] leading-tight text-foreground">{workspace?.name ?? "Workspace"}</p>
            {me.user?.email ? <p className="truncate font-mono text-[11px] text-muted-foreground">{me.user.email}</p> : null}
          </div>
          <button type="button" onClick={signOut} className="flex items-center gap-2 rounded-control px-2 py-1.5 text-left text-body-regular text-foreground hover:bg-muted">
            <RiLogoutBoxRLine aria-hidden className="size-4 text-muted-foreground" />
            Sign out
          </button>
        </div>
      </Popover>
    </PopoverTrigger>
  );
}
