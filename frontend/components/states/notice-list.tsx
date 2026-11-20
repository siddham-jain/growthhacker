"use client";

import { RiAlertLine, RiInformationLine } from "@remixicon/react";
import type { RunNotice } from "@/lib/api/types";
import { cx } from "@/lib/cx";

// known codes get product copy; anything new falls back to the server's own message
const NOTICE_COPY: Record<string, { title: string; body?: string }> = {
  quota_limited: { title: "Today's YouTube data budget is used up.", body: "You're seeing the last data we fetched. Fresh data arrives after the daily reset." },
  news_skipped: { title: "The news scan didn't run today.", body: "We'll try again at the next refresh." },
  llm_fallback: { title: "Some cards use plain wording today.", body: "The numbers and evidence are unchanged." },
  partial_profile: { title: "We couldn't read all of your website.", body: "Add anything we missed in Settings → Business profile." },
  news_partial: { title: "The news scan ran with gaps." },
  stale_config: { title: "Your settings changed since this analysis ran.", body: "Re-run analysis to use them." },
};

const HANDLED_ELSEWHERE = new Set(["thin_data"]);

export function NoticeList({ notices, className }: { notices: RunNotice[]; className?: string }) {
  const shown = notices.filter((notice) => !HANDLED_ELSEWHERE.has(notice.code));
  if (!shown.length) return null;
  return (
    <ul className={cx("flex flex-col gap-2", className)} aria-label="Notices">
      {shown.map((notice) => {
        const copy = NOTICE_COPY[notice.code];
        const warning = notice.level !== "info";
        const Icon = warning ? RiAlertLine : RiInformationLine;
        return (
          <li
            key={`${notice.code}-${notice.message}`}
            className={cx(
              "flex items-start gap-2.5 rounded-control px-3 py-2.5 text-body-regular",
              warning ? "bg-[color-mix(in_oklab,var(--warning)_10%,var(--card))] text-warning" : "bg-muted text-muted-foreground",
            )}
          >
            <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span>
              <span className="font-medium">{copy?.title ?? "Done, with gaps."}</span> {copy?.body ?? notice.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
