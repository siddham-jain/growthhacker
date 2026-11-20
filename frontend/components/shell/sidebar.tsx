"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { RiAddLine, RiArrowDownSLine, RiSearchLine } from "@remixicon/react";
import { clientsQuery } from "@/lib/api/queries";
import type { Client } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { API_MOCK } from "@/lib/env";
import { AccountMenu, ThemeToggle } from "./lazy-chrome";
import { useWorkspaceSlug } from "./workspace";
import { openCommandMenu } from "./command-menu";
import { NAV_ITEMS } from "./nav";
import { LifecycleDot } from "./lifecycle-dot";

export function Brand({ compact = false }: { compact?: boolean }) {
  const slug = useWorkspaceSlug();
  return (
    <Link href={`/w/${slug}/clients`} className="flex items-center gap-2 rounded-control px-1 py-1">
      <span aria-hidden className="grid size-7 place-items-center rounded-full bg-primary text-[13px] font-semibold text-primary-foreground">
        G
      </span>
      <span className={cx("font-heading text-[17px] font-semibold tracking-tight", compact && "lg:max-xl:sr-only")}>GrowthHacker</span>
    </Link>
  );
}

function ClientSwitcher({ client }: { client: Client | undefined }) {
  const clients = useQuery(clientsQuery());
  const slug = useWorkspaceSlug();
  return (
    <details className="group relative lg:max-xl:hidden">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-control border border-border bg-card px-2.5 py-2 text-body-medium shadow-xs">
        {client ? <LifecycleDot lifecycle={client.lifecycle} /> : null}
        <span className="min-w-0 flex-1 truncate">{client?.name ?? "Choose a client"}</span>
        <RiArrowDownSLine aria-hidden className="size-4 text-subtle-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="absolute inset-x-0 top-full z-30 mt-1 rounded-control border border-border bg-card p-1 shadow-overlay">
        {clients.data?.items.map((item) => (
          <Link key={item.id} href={`/c/${item.id}/today`} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-body-regular hover:bg-muted">
            <LifecycleDot lifecycle={item.lifecycle} />
            <span className="truncate">{item.name}</span>
          </Link>
        ))}
        <Link href={`/w/${slug}/new`} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-body-regular text-muted-foreground hover:bg-muted">
          <RiAddLine aria-hidden className="size-4" />
          Add client
        </Link>
      </div>
    </details>
  );
}

export function SidebarNav({ clientId, onNavigate }: { clientId: string; onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Sections" className="flex flex-col gap-0.5">
      {NAV_ITEMS.map(({ segment, label, icon: Icon }) => {
        const href = `/c/${clientId}/${segment}`;
        const active = pathname.startsWith(href);
        return (
          <Link
            key={segment}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            title={label}
            className={cx(
              "flex items-center gap-2.5 rounded-control px-2.5 py-2 text-body-regular transition-colors",
              active ? "bg-card text-foreground shadow-card" : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-[18px] shrink-0" />
            <span className="lg:max-xl:sr-only">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function Sidebar({ client, clientId }: { client: Client | undefined; clientId: string }) {
  return (
    <aside className="sticky top-0 hidden h-dvh shrink-0 flex-col gap-5 border-r border-border bg-sidebar px-3 py-4 lg:flex lg:w-16 xl:w-60">
      <Brand compact />
      <ClientSwitcher client={client} />
      <button
        type="button"
        onClick={openCommandMenu}
        title="Search and commands"
        className="flex items-center gap-2 rounded-control border border-border bg-card px-2.5 py-1.5 text-body-regular text-muted-foreground shadow-xs transition-colors hover:text-foreground lg:max-xl:justify-center"
      >
        <RiSearchLine aria-hidden className="size-4 shrink-0" />
        <span className="flex-1 text-left lg:max-xl:sr-only">Search</span>
        <kbd className="rounded-sm bg-muted px-1.5 font-sans text-caption-2-medium text-muted-foreground lg:max-xl:hidden">⌘K</kbd>
      </button>
      <SidebarNav clientId={clientId} />
      <div className="mt-auto flex flex-col gap-3 lg:max-xl:items-center">
        <AccountMenu compact />
        {API_MOCK ? (
          <p className="text-caption-1-regular text-subtle-foreground lg:max-xl:hidden">Demo data · Apollo Hospitals fixtures</p>
        ) : null}
        <ThemeToggle appearance="sidebar-segmented" size="small" />
      </div>
    </aside>
  );
}
