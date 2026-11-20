"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

const OPEN_EVENT = "gh:open-command-menu";

// the palette (and its menu and search code) loads on first use, keeping it out of every page's first load
const CommandPalettePanel = dynamic(() => import("./command-palette-panel"), { ssr: false });

export function openCommandMenu() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

export function CommandMenu({ clientId }: { clientId: string }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const show = () => {
      setLoaded(true);
      setOpen(true);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setLoaded(true);
        setOpen((current) => !current);
      }
    };
    window.addEventListener(OPEN_EVENT, show);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(OPEN_EVENT, show);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  return loaded ? <CommandPalettePanel clientId={clientId} open={open} onOpenChange={setOpen} /> : null;
}
