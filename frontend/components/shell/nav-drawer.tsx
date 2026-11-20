"use client";

import { Drawer, DrawerBody } from "@/components/ui/primitives";
import { SidebarNav } from "./sidebar";

export default function NavDrawer({ clientId, isOpen, onOpenChange }: { clientId: string; isOpen: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Drawer placement="start" size="sm" isOpen={isOpen} onOpenChange={onOpenChange} aria-label="Navigation">
      <DrawerBody>
        <SidebarNav clientId={clientId} onNavigate={() => onOpenChange(false)} />
      </DrawerBody>
    </Drawer>
  );
}
