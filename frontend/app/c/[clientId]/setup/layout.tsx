import type { ReactNode } from "react";
import { PlainShell } from "@/components/shell/plain-shell";

export default function Layout({ children }: { children: ReactNode }) {
  return <PlainShell>{children}</PlainShell>;
}
