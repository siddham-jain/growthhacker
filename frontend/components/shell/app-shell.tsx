"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { RiMenuLine } from "@remixicon/react";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerBody } from "@/components/ui/primitives";
import { clientQuery } from "@/lib/api/queries";
import { Brand, Sidebar, SidebarNav } from "./sidebar";

function MobileBar({ clientId }: { clientId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sticky top-0 z-20 flex items-center justify-between border-b border-border bg-background/90 px-4 py-2 backdrop-blur lg:hidden">
      <Brand />
      <Button variant="secondary" size="small" iconOnly leadingIcon={RiMenuLine} aria-label="Open navigation" onClick={() => setOpen(true)} />
      <Drawer placement="start" size="sm" isOpen={open} onOpenChange={setOpen} aria-label="Navigation">
        <DrawerBody>
          <SidebarNav clientId={clientId} onNavigate={() => setOpen(false)} />
        </DrawerBody>
      </Drawer>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { clientId } = useParams<{ clientId: string }>();
  const client = useQuery(clientQuery(clientId));
  return (
    <div className="flex min-h-dvh">
      <Sidebar client={client.data} clientId={clientId} />
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileBar clientId={clientId} />
        <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 pt-6 pb-16 sm:px-6 lg:px-10 lg:pt-10">{children}</main>
        <footer className="mx-auto w-full max-w-[1200px] px-4 pb-6 text-caption-1-regular text-subtle-foreground sm:px-6 lg:px-10">
          Video data from YouTube. Ratios and ranges are calculated by GrowthHacker.
        </footer>
      </div>
    </div>
  );
}
