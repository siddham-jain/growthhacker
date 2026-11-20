"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { SegmentedControl, SegmentedControlItem, Select, SelectItem, SwitchCard, toast } from "@/components/ui/primitives";
import { TextField } from "@/components/ui/text-field";
import { api } from "@/lib/api";
import { clientKey, notificationSettingsQuery } from "@/lib/api/queries";
import type { Client, ClientPatch, NotificationSettings, NotificationSettingsPatch, WindowDays } from "@/lib/api/types";
import { SettingsSection } from "./section";

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function ScheduleSettings({ client, notifications, onChanged }: { client: Client; notifications: NotificationSettings; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState(notifications.email ?? "");
  const [emailError, setEmailError] = useState<string | null>(null);

  const patchClient = useMutation({
    mutationFn: async (patch: ClientPatch) => api.clients.update(client.id, patch, client.version),
    onSuccess: (updated, patch) => {
      queryClient.setQueryData(clientKey(client.id), updated);
      if (patch.window_days) onChanged();
      toast.success("Saved");
    },
    onError: () => toast.error("Couldn't save. Try again."),
  });
  const patchNotifications = useMutation({
    mutationFn: async (patch: NotificationSettingsPatch) => api.settings.updateNotifications(client.id, patch),
    onSuccess: (next) => {
      queryClient.setQueryData(notificationSettingsQuery(client.id).queryKey, next);
      toast.success("Notification settings saved");
    },
    onError: () => toast.error("Couldn't save. Try again."),
  });

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <SettingsSection title="Analysis cadence" description={`Times are in ${client.market.timezone}.`}>
        <Select label="Daily refresh" selectedKey={String(client.refresh_hour_local)} onSelectionChange={(key) => key !== null && patchClient.mutate({ refresh_hour_local: Number(key) })} className="w-48">
          {HOURS.map((hour) => (
            <SelectItem key={hour} id={String(hour)} textValue={hourLabel(hour)}>
              {hourLabel(hour)}
            </SelectItem>
          ))}
        </Select>
        <div className="flex flex-col gap-1.5">
          <span className="text-body-medium text-foreground" id="default-window">
            Default window
          </span>
          <SegmentedControl aria-labelledby="default-window" selectedKeys={[String(client.window_days)]} onSelectionChange={(keys) => patchClient.mutate({ window_days: Number([...keys][0]) as WindowDays })}>
            <SegmentedControlItem id="7">7 days</SegmentedControlItem>
            <SegmentedControlItem id="30">30 days</SegmentedControlItem>
            <SegmentedControlItem id="90">90 days</SegmentedControlItem>
          </SegmentedControl>
          <p className="text-caption-1-regular text-muted-foreground">Every screen opens on this window. The selector at the top of each page changes your view only.</p>
        </div>
        <SwitchCard
          title="Pause this client"
          description="Stops daily refreshes and news scans. Data older than 30 days is removed while paused, as YouTube's rules require."
          isSelected={client.paused}
          onChange={(paused) => patchClient.mutate({ paused })}
        />
      </SettingsSection>
      <SettingsSection title="Notifications" description={notifications.delivery_enabled ? "Sent by email to the address below." : "Email delivery isn't switched on for this server yet. Your choices are saved and apply once it is."}>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const value = email.trim();
            if (value && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) {
              setEmailError("Enter a valid email address.");
              return;
            }
            setEmailError(null);
            patchNotifications.mutate({ email: value || null });
          }}
        >
          <TextField label="Send to" type="email" value={email} onChange={(event) => setEmail(event.target.value)} error={emailError} placeholder="you@company.com" className="flex-1" />
          <Button type="submit" variant="secondary" size="small" className="mb-0.5" disabled={patchNotifications.isPending}>
            Save
          </Button>
        </form>
        <SwitchCard title="First brief is ready" description="Once, after setup finishes." isSelected={notifications.first_brief_ready} onChange={(value) => patchNotifications.mutate({ first_brief_ready: value })} />
        <SwitchCard title="Daily brief" description={`A short digest after the ${hourLabel(client.refresh_hour_local)} refresh.`} isSelected={notifications.daily_digest} onChange={(value) => patchNotifications.mutate({ daily_digest: value })} />
        <SwitchCard title="News closing soon" description="When a news opening has less than a day left." isSelected={notifications.news_closing_soon} onChange={(value) => patchNotifications.mutate({ news_closing_soon: value })} />
        {client.paused ? <InfoNote>Paused clients don&apos;t send notifications.</InfoNote> : null}
      </SettingsSection>
    </div>
  );
}
