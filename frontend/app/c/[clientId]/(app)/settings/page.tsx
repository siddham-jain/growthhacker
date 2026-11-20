"use client";

import { RiRefreshLine } from "@remixicon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { SettingsSection } from "@/components/settings/section";
import { useActiveWorkspace } from "@/components/shell/workspace";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState, LoadingBlock } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Chip, Tab, TabList, TabPanel, Tabs, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { buyerQuestionsQuery, clientKey, clientQuery, competitorsQuery, keywordsQuery, llmSettingsQuery, notificationSettingsQuery, profileQuery } from "@/lib/api/queries";
import { useTabParam } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";
import { oauthReason, startYouTubeConnect } from "@/lib/youtube-connect";

// each tab's editor renders after its own fetch, so its code (selects, switches) loads alongside
const loadingEditor = () => <LoadingBlock />;
const ProfileEditor = dynamic(() => import("@/components/settings/profile-editor").then((module) => module.ProfileEditor), { ssr: false, loading: loadingEditor });
const TopicsEditor = dynamic(() => import("@/components/settings/topics-editor").then((module) => module.TopicsEditor), { ssr: false, loading: loadingEditor });
const CompetitorsEditor = dynamic(() => import("@/components/settings/competitors-editor").then((module) => module.CompetitorsEditor), { ssr: false, loading: loadingEditor });
const EngineSettings = dynamic(() => import("@/components/settings/engine-settings").then((module) => module.EngineSettings), { ssr: false, loading: loadingEditor });
const ScheduleSettings = dynamic(() => import("@/components/settings/schedule-settings").then((module) => module.ScheduleSettings), { ssr: false, loading: loadingEditor });

const TABS = ["profile", "topics", "competitors", "integrations", "engine", "schedule"] as const;

// edits collapse into one banner; the analysis only re-runs when the user asks
function RerunBanner({ clientId, activeRunId }: { clientId: string; activeRunId: string | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const rerun = useMutation({
    mutationFn: async () => api.runs.create(clientId, { kind: "analysis" }, crypto.randomUUID()),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: [...clientKey(clientId), "runs"] });
      toast("Analysis started", { description: "About two minutes.", actions: [{ label: "Watch", onClick: () => router.push(`/c/${clientId}/setup/analysis?run=${run.id}`) }] });
    },
    onError: () => toast.error("Couldn't start the analysis. Try again."),
  });
  return (
    <div role="status" className="mb-5 flex flex-col gap-3 rounded-card bg-[color-mix(in_oklab,var(--info)_8%,var(--card))] px-4 py-3 shadow-card sm:flex-row sm:items-center sm:justify-between">
      <p className="text-body-regular text-foreground">
        {activeRunId ? "We'll use your changes in the next run." : "Your settings changed. Re-run analysis to update recommendations (about 2 minutes)."}
      </p>
      {rerun.isSuccess ? null : (
        <Button variant="primary" size="small" leadingIcon={RiRefreshLine} onClick={() => rerun.mutate()} disabled={rerun.isPending}>
          {activeRunId ? "Re-run after this one" : "Re-run analysis"}
        </Button>
      )}
    </div>
  );
}

function YouTubeIntegration({ clientId, connected, canEdit }: { clientId: string; connected: boolean; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const disconnect = useMutation({
    mutationFn: () => api.integrations.youtubeDisconnect(clientId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: clientKey(clientId) });
      toast.success("YouTube disconnected", { description: "Owner analytics for this client were deleted." });
    },
    onError: (caught) => toast.error(isApiError(caught) ? caught.message : "Couldn't disconnect. Try again."),
  });
  return (
    <SettingsSection
      title="YouTube owner analytics"
      description="Connecting adds exact day-N views, subscribers gained, traffic sources and search terms. They're shown only to your workspace and never mixed with public numbers."
      action={connected ? <Chip variant="caption" color="lime">Connected</Chip> : null}
    >
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size="small" disabled={!canEdit} onClick={() => startYouTubeConnect(clientId)}>
          {connected ? "Reconnect" : "Connect with YouTube"}
        </Button>
        {connected ? (
          <Button variant="ghost" size="small" disabled={!canEdit || disconnect.isPending} onClick={() => disconnect.mutate()}>
            Disconnect
          </Button>
        ) : null}
      </div>
    </SettingsSection>
  );
}

// google sends the browser back here with ?oauth=connected or ?oauth=error&reason=…
function useOAuthResult(clientId: string) {
  const params = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const outcome = params.get("oauth");
  const reason = params.get("reason");
  useEffect(() => {
    if (!outcome) return;
    if (outcome === "connected") {
      queryClient.invalidateQueries({ queryKey: clientKey(clientId) });
      toast.success("YouTube connected", { description: "Owner analytics arrive with the next analysis." });
    } else {
      toast.error("YouTube wasn't connected", { description: oauthReason(reason) });
    }
    router.replace(`/c/${clientId}/settings?tab=integrations`, { scroll: false });
  }, [clientId, outcome, queryClient, reason, router]);
}

export default function SettingsPage() {
  const clientId = useClientId();
  const [tab, setTab] = useTabParam(TABS, "profile");
  const [changed, setChanged] = useState(false);
  const markChanged = () => setChanged(true);
  const { me } = useActiveWorkspace();
  const client = useQuery(clientQuery(clientId));
  const profile = useQuery({ ...profileQuery(clientId), enabled: tab === "profile" });
  const keywords = useQuery({ ...keywordsQuery(clientId), enabled: tab === "topics" });
  const questions = useQuery({ ...buyerQuestionsQuery(clientId), enabled: tab === "topics" });
  const competitors = useQuery({ ...competitorsQuery(clientId), enabled: tab === "competitors" });
  const llm = useQuery({ ...llmSettingsQuery(), enabled: tab === "engine" });
  const notifications = useQuery({ ...notificationSettingsQuery(clientId), enabled: tab === "schedule" });
  const canEdit = me?.role !== "viewer";
  useOAuthResult(clientId);

  return (
    <>
      <PageHeader title="Settings" subtitle="Your business profile, topics, competitors, connections and how often we look." controls="none" />
      {changed ? <RerunBanner clientId={clientId} activeRunId={client.data?.active_run_id ?? null} /> : null}
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
        <TabList aria-label="Settings sections">
          <Tab id="profile">Business profile</Tab>
          <Tab id="topics">Keywords and questions</Tab>
          <Tab id="competitors">Competitors</Tab>
          <Tab id="integrations">Integrations</Tab>
          <Tab id="engine">Analysis engine</Tab>
          <Tab id="schedule">Schedule and notifications</Tab>
        </TabList>
        <TabPanel id="profile">
          {profile.isPending ? <LoadingBlock /> : null}
          {profile.error ? <ErrorState error={profile.error} onRetry={() => profile.refetch()} /> : null}
          {profile.data ? <ProfileEditor clientId={clientId} view={profile.data} onChanged={markChanged} /> : null}
        </TabPanel>
        <TabPanel id="topics">
          {keywords.isPending || questions.isPending ? <LoadingBlock /> : null}
          {keywords.data && questions.data ? <TopicsEditor clientId={clientId} keywords={keywords.data.items} questions={questions.data.items} onChanged={markChanged} /> : null}
        </TabPanel>
        <TabPanel id="competitors">
          {competitors.isPending ? <LoadingBlock /> : null}
          {competitors.data ? <CompetitorsEditor clientId={clientId} competitors={competitors.data.items} onChanged={markChanged} /> : null}
        </TabPanel>
        <TabPanel id="integrations">
          <YouTubeIntegration clientId={clientId} connected={Boolean(client.data?.oauth_connected)} canEdit={canEdit} />
        </TabPanel>
        <TabPanel id="engine">
          {llm.isPending ? <LoadingBlock /> : null}
          {llm.error ? <ErrorState error={llm.error} onRetry={() => llm.refetch()} /> : null}
          {llm.data ? <EngineSettings settings={llm.data} canEdit={me?.role === "owner"} /> : null}
        </TabPanel>
        <TabPanel id="schedule">
          {notifications.isPending || !client.data ? <LoadingBlock /> : null}
          {client.data && notifications.data ? (
            <fieldset disabled={!canEdit} className="contents">
              <ScheduleSettings client={client.data} notifications={notifications.data} onChanged={markChanged} />
            </fieldset>
          ) : null}
        </TabPanel>
      </Tabs>
    </>
  );
}
