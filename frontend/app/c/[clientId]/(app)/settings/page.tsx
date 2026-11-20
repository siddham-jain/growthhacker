"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ProfileReview } from "@/components/onboarding/profile-review";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState, InfoNote, LoadingBlock } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Chip, SegmentedControl, SegmentedControlItem, Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { buyerQuestionsQuery, clientKey, clientQuery, competitorsQuery, keywordsQuery, profileQuery } from "@/lib/api/queries";
import type { CompetitorRole, TopicStatus, WindowDays } from "@/lib/api/types";
import { RELATION_LABELS } from "@/lib/labels";
import { useTabParam } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["profile", "topics", "competitors", "integrations", "schedule"] as const;

const STATUS_COLOR: Record<TopicStatus, "lime" | "soft" | "rose"> = { active: "lime", suggested: "soft", rejected: "rose" };
const ROLE_LABELS: Record<CompetitorRole, string> = { competitor_set: "Competitor", niche_set: "Niche", benchmark_set: "Benchmark" };

function TopicList({ title, items }: { title: string; items: { id: string; text: string; status: TopicStatus }[] }) {
  return (
    <section className="rounded-card bg-card p-4 shadow-card">
      <h2 className="mb-2 text-body-medium text-foreground">
        {title} <span className="text-muted-foreground">· {items.filter((item) => item.status === "active").length} active</span>
      </h2>
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-body-regular">
            <span>{item.text}</span>
            <Chip variant="caption" color={STATUS_COLOR[item.status]}>
              {item.status}
            </Chip>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function SettingsPage() {
  const clientId = useClientId();
  const queryClient = useQueryClient();
  const [tab, setTab] = useTabParam(TABS, "profile");
  const client = useQuery(clientQuery(clientId));
  const profile = useQuery(profileQuery(clientId));
  const keywords = useQuery(keywordsQuery(clientId));
  const questions = useQuery(buyerQuestionsQuery(clientId));
  const competitors = useQuery(competitorsQuery(clientId));
  const updateWindow = useMutation({
    mutationFn: (windowDays: WindowDays) => api.clients.update(clientId, { window_days: windowDays }, client.data?.version ?? 0),
    onSuccess: (updated) => queryClient.setQueryData(clientKey(clientId), updated),
  });

  return (
    <>
      <PageHeader title="Settings" subtitle="Your business profile, topics, competitors and connections." controls="none" />
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
        <TabList aria-label="Settings sections">
          <Tab id="profile">Business profile</Tab>
          <Tab id="topics">Keywords and questions</Tab>
          <Tab id="competitors">Competitors</Tab>
          <Tab id="integrations">Integrations</Tab>
          <Tab id="schedule">Schedule</Tab>
        </TabList>
        <TabPanel id="profile">
          {profile.isPending ? <LoadingBlock /> : null}
          {profile.error ? <ErrorState error={profile.error} onRetry={() => profile.refetch()} /> : null}
          {profile.data ? <ProfileReview view={profile.data} /> : null}
        </TabPanel>
        <TabPanel id="topics">
          <div className="grid gap-4 lg:grid-cols-2">
            <TopicList title="Buyer questions" items={questions.data?.items ?? []} />
            <TopicList title="Keywords" items={keywords.data?.items ?? []} />
          </div>
        </TabPanel>
        <TabPanel id="competitors">
          <ul className="divide-y divide-border rounded-card bg-card shadow-card">
            {(competitors.data?.items ?? []).map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
                <span className="text-body-medium text-foreground">{item.name}</span>
                <Chip variant="caption" color="soft">
                  {ROLE_LABELS[item.role]} · {RELATION_LABELS[item.relation]}
                </Chip>
                <span className="ml-auto text-caption-1-regular text-muted-foreground">{item.status}</span>
              </li>
            ))}
          </ul>
        </TabPanel>
        <TabPanel id="integrations">
          <div className="flex flex-col gap-3 rounded-card bg-card p-5 shadow-card">
            <p className="text-body-medium text-foreground">YouTube owner analytics</p>
            <p className="text-body-regular text-muted-foreground">
              Connecting adds exact day-N views, subscribers gained, traffic sources and search terms. They&apos;re shown only to your workspace and never mixed with
              public numbers.
            </p>
            <Button variant="primary" size="small" className="w-fit" onClick={() => api.integrations.youtubeConnect(clientId)}>
              {client.data?.oauth_connected ? "Reconnect" : "Connect with YouTube"}
            </Button>
          </div>
        </TabPanel>
        <TabPanel id="schedule">
          <div className="flex flex-col gap-4 rounded-card bg-card p-5 shadow-card">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-body-medium text-foreground">Default window</span>
              <SegmentedControl
                aria-label="Default window"
                selectedKeys={[String(client.data?.window_days ?? 30)]}
                onSelectionChange={(keys) => updateWindow.mutate(Number([...keys][0]) as WindowDays)}
              >
                <SegmentedControlItem id="7">7 days</SegmentedControlItem>
                <SegmentedControlItem id="30">30 days</SegmentedControlItem>
                <SegmentedControlItem id="90">90 days</SegmentedControlItem>
              </SegmentedControl>
            </div>
            <p className="text-body-regular text-muted-foreground">Daily refresh at {client.data?.refresh_hour_local ?? 6}:00 local time.</p>
            {updateWindow.isSuccess ? <InfoNote>Saved. Re-run analysis to update recommendations (about 2 minutes).</InfoNote> : null}
          </div>
        </TabPanel>
      </Tabs>
    </>
  );
}
