"use client";

import dynamic from "next/dynamic";

// sidebar chrome that needs react-aria loads after hydration, so screens' first load stays small
export const AccountMenu = dynamic(() => import("./account-menu"), { ssr: false });

export const ThemeToggle = dynamic(() => import("@/components/ui/theme-toggle").then((module) => module.ThemeToggle), {
  ssr: false,
  loading: () => <span aria-hidden className="block h-8 w-[4.5rem]" />,
});
