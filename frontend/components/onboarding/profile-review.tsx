import { InfoNote } from "@/components/states/states";
import { Chip } from "@/components/ui/primitives";
import { SourceKindChip } from "@/components/ui/chips";
import type { BusinessProfileView, ProfileFact } from "@/lib/api/types";
import { isMeaningfulContact } from "@/lib/contact";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import { SourcesUsed } from "./sources-used";

const GROUPS = [
  { key: "offerings", label: "Offerings" },
  { key: "locations", label: "Locations" },
  { key: "conversion_actions", label: "Ways customers reach you" },
  { key: "value_propositions", label: "Why customers choose you" },
] as const;

function FactRow({ fact }: { fact: ProfileFact }) {
  const evidence = fact.meta.evidence[0];
  const needsConfirmation = fact.meta.status === "needs_confirmation";
  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <a
        href={evidence?.url}
        target="_blank"
        rel="noreferrer"
        title={evidence ? `“${evidence.quote}”` : undefined}
        className="text-body-regular text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground"
      >
        {fact.label}
      </a>
      {evidence ? <SourceKindChip kind={evidence.source_kind} /> : null}
      {needsConfirmation ? (
        <Chip variant="caption" color="yellow" title={evidence ? SOURCE_KIND_LABELS[evidence.source_kind].description : undefined}>
          Please confirm
        </Chip>
      ) : null}
    </li>
  );
}

// profiles built before the contact rules can still hold form buttons and bare dialling codes
function visibleFacts(view: BusinessProfileView, key: (typeof GROUPS)[number]["key"]): ProfileFact[] {
  const facts = view.effective[key];
  return key === "conversion_actions" ? facts.filter((fact) => isMeaningfulContact(fact.label)) : facts;
}

export function ProfileReview({ view }: { view: BusinessProfileView }) {
  const profile = view.effective;
  return (
    <div className="flex flex-col gap-5">
      <p className="text-body-regular text-foreground">{profile.identity.one_liner}</p>
      <SourcesUsed sources={view.sources} />
      {view.partial || view.completeness < 0.6 ? (
        <InfoNote tone="warning">Your site limits automated reading, so we filled gaps from other public sources. Facts marked “Please confirm” came from outside your site.</InfoNote>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        {GROUPS.map((group) => {
          const facts = visibleFacts(view, group.key);
          return (
            <section key={group.key} aria-label={group.label} className="rounded-card bg-card p-4 shadow-card">
              <h3 className="text-body-medium text-foreground">
                {group.label} <span className="text-muted-foreground">· {facts.length}</span>
              </h3>
              <ul className="divide-y divide-border">
                {facts.map((fact) => (
                  <FactRow key={fact.id} fact={fact} />
                ))}
              </ul>
            </section>
          );
        })}
      </div>
      {profile.freshness.regulated_domain ? (
        <InfoNote>Healthcare is regulated, so news ideas and description changes will ask for review before publishing.</InfoNote>
      ) : null}
    </div>
  );
}
