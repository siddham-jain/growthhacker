"use client";

import { useQuery } from "@tanstack/react-query";
import { meQuery } from "@/lib/api/queries";
import { DEMO_WORKSPACE } from "@/lib/env";

export function useActiveWorkspace() {
  const me = useQuery(meQuery());
  const workspace = me.data?.workspaces.find((item) => item.id === me.data?.active_workspace_id) ?? me.data?.workspaces[0];
  return { me: me.data, workspace };
}

export function useWorkspaceSlug(): string {
  return useActiveWorkspace().workspace?.slug ?? DEMO_WORKSPACE;
}
