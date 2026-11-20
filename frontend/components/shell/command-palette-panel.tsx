"use client";

import { RiAddLine, RiArrowGoForwardLine, RiBuilding2Line, RiCalendarLine, RiRefreshLine } from "@remixicon/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import type { Key } from "react-aria-components";
import { CommandPalette, CommandPaletteItem, CommandPaletteSection, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { clientKey, clientsQuery } from "@/lib/api/queries";
import { NAV_ITEMS } from "./nav";
import { useWorkspaceSlug } from "./account-menu";

// sections, clients and a few actions; every action is something a header button already does
export default function CommandPalettePanel({ clientId, open, onOpenChange }: { clientId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const slug = useWorkspaceSlug();
  const clients = useQuery(clientsQuery());
  function withView(path: string) {
    const window = params.get("w");
    return window ? `${path}?w=${window}` : path;
  }

  async function rerun() {
    try {
      const run = await api.runs.create(clientId, { kind: "analysis" }, crypto.randomUUID());
      queryClient.invalidateQueries({ queryKey: [...clientKey(clientId), "runs"] });
      toast("Analysis started", { description: "About two minutes. Keep working; results update when it's done.", actions: [{ label: "Watch", onClick: () => router.push(`/c/${clientId}/setup/analysis?run=${run.id}`) }] });
    } catch {
      toast.error("Couldn't start the analysis. Try again.");
    }
  }

  function run(key: Key) {
    const id = String(key);
    if (id.startsWith("nav:")) router.push(withView(`/c/${clientId}/${id.slice(4)}`));
    else if (id.startsWith("client:")) router.push(`/c/${id.slice(7)}/today`);
    else if (id.startsWith("window:")) {
      const next = new URLSearchParams(params.toString());
      next.set("w", id.slice(7));
      router.replace(`?${next.toString()}`, { scroll: false });
    } else if (id === "rerun") void rerun();
    else if (id === "add-client") router.push(`/w/${slug}/new`);
    else if (id === "all-clients") router.push(`/w/${slug}/clients`);
  }

  return (
    <CommandPalette isOpen={open} onOpenChange={onOpenChange} shortcut={false} onAction={run} placeholder="Jump to a section, a client or an action…" aria-label="Search and commands">
      <CommandPaletteSection title="Sections">
        {NAV_ITEMS.map(({ segment, label, icon }) => (
          <CommandPaletteItem key={segment} id={`nav:${segment}`} icon={icon} textValue={label}>
            {label}
          </CommandPaletteItem>
        ))}
      </CommandPaletteSection>
      <CommandPaletteSection title="Actions">
        <CommandPaletteItem id="window:30" icon={RiCalendarLine} textValue="Widen to 30 days">
          Widen to 30 days
        </CommandPaletteItem>
        <CommandPaletteItem id="window:90" icon={RiCalendarLine} textValue="Widen to 90 days">
          Widen to 90 days
        </CommandPaletteItem>
        <CommandPaletteItem id="rerun" icon={RiRefreshLine} textValue="Re-run analysis" description="about 2 min">
          Re-run analysis
        </CommandPaletteItem>
        <CommandPaletteItem id="add-client" icon={RiAddLine} textValue="Add a client">
          Add a client
        </CommandPaletteItem>
      </CommandPaletteSection>
      <CommandPaletteSection title="Clients">
        {(clients.data?.items ?? []).map((client) => (
          <CommandPaletteItem key={client.id} id={`client:${client.id}`} icon={RiBuilding2Line} textValue={client.name} description={client.id === clientId ? "current" : undefined}>
            {client.name}
          </CommandPaletteItem>
        ))}
        <CommandPaletteItem id="all-clients" icon={RiArrowGoForwardLine} textValue="All clients">
          All clients
        </CommandPaletteItem>
      </CommandPaletteSection>
    </CommandPalette>
  );
}
