"use client";

import { useQuery } from "@tanstack/react-query";
import { RiAddLine } from "@remixicon/react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { lifecycleLabel, LifecycleDot } from "@/components/shell/lifecycle-dot";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { LinkButton } from "@/components/ui/button";
import { clientsQuery } from "@/lib/api/queries";
import { formatRelative } from "@/lib/format";

export default function ClientsPage() {
  const { slug } = useParams<{ slug: string }>();
  const clients = useQuery(clientsQuery());
  const items = clients.data?.items ?? [];
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-greeting text-foreground">Clients</h1>
          <p className="mt-1 text-body-regular text-muted-foreground">Every business you advise, with its latest brief.</p>
        </div>
        <LinkButton href={`/w/${slug}/new`} variant="primary" size="medium" trailingIcon={RiAddLine}>
          Add client
        </LinkButton>
      </div>
      {clients.isPending ? <LoadingBlock /> : null}
      {clients.error ? <ErrorState error={clients.error} onRetry={() => clients.refetch()} /> : null}
      {clients.data && !items.length ? (
        <EmptyState title="No clients yet" body="Add a business with its website, YouTube channel or just its name." action={<LinkButton href={`/w/${slug}/new`}>Add your first client</LinkButton>} />
      ) : null}
      <ul className="grid gap-3 md:grid-cols-2">
        {items.map((client) => (
          <li key={client.id}>
            <Link href={client.lifecycle === "onboarding" ? `/c/${client.id}/setup/analyze` : `/c/${client.id}/ideas`} className="flex flex-col gap-2 rounded-card bg-card p-5 shadow-card transition-shadow hover:shadow-raised">
              <span className="flex items-center gap-2">
                <LifecycleDot lifecycle={client.lifecycle} />
                <span className="text-headline text-foreground">{client.name}</span>
              </span>
              <span className="text-caption-1-regular text-muted-foreground">
                {lifecycleLabel(client.lifecycle)} · {client.own_channel?.handle ?? "no channel yet"} · {client.window_days}-day window
                {client.last_success_at ? ` · refreshed ${formatRelative(client.last_success_at)}` : ""}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
