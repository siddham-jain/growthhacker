"use client";

import { useQuery } from "@tanstack/react-query";
import { OriginChip } from "@/components/ui/chips";
import { CalcNote } from "@/components/ui/calc-mark";
import { Chip } from "@/components/ui/primitives";
import { ErrorState, LoadingBlock } from "@/components/states/states";
import { answerCitationsQuery } from "@/lib/api/queries";
import type { AnswerCitations, CitedVideo, QuestionCitations } from "@/lib/api/types";
import { formatDate, plural } from "@/lib/format";

const STATUS: Record<QuestionCitations["status"], { label: string; color: "lime" | "orange" | "gray" }> = {
  cites_you: { label: "Cites you", color: "lime" },
  competitor_only: { label: "Gap: a competitor, not you", color: "orange" },
  others_only: { label: "Other channels only", color: "gray" },
  no_youtube: { label: "No YouTube video cited", color: "gray" },
};

const OWNER_LABEL: Record<CitedVideo["owner"], string> = { own: "You", competitor: "Competitor", other: "Other channel" };

function CheckedLine({ data }: { data: AnswerCitations }) {
  return (
    <p className="text-caption-1-regular text-muted-foreground">
      {data.last_checked_at ? `last checked ${formatDate(data.last_checked_at)}` : "not checked yet"} · up to 10 of your top buyer questions a week · {data.source_label}
    </p>
  );
}

function NotCheckedYet() {
  return (
    <p className="text-body-regular text-muted-foreground">
      We haven&apos;t checked any buyer questions yet. Each week we search up to 10 of your top questions and note which YouTube videos the answers cite.
    </p>
  );
}

function CitedVideoLine({ video }: { video: CitedVideo }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-caption-1-regular">
      <Chip variant="caption" color={video.owner === "own" ? "lime" : video.owner === "competitor" ? "orange" : "soft"}>
        {OWNER_LABEL[video.owner]}
      </Chip>
      <a href={video.video.url} target="_blank" rel="noreferrer" className="text-foreground hover:underline">
        {video.video.title ?? video.video.video_id}
      </a>
      {video.video.channel_title ? <span className="text-muted-foreground">{video.video.channel_title}</span> : null}
    </li>
  );
}

function QuestionRow({ question }: { question: QuestionCitations }) {
  const status = STATUS[question.status];
  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-body-medium text-foreground">{question.text}</p>
        <Chip variant="caption" color={status.color}>
          {status.label}
        </Chip>
      </div>
      {question.videos.length ? (
        <ul className="flex flex-col gap-1">
          {question.videos.map((video) => (
            <CitedVideoLine key={video.video.video_id} video={video} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

// the gaps come first: questions where a competitor's video is cited and yours isn't
const ORDER: QuestionCitations["status"][] = ["competitor_only", "cites_you", "others_only", "no_youtube"];

export function YourAnswerCitations({ clientId }: { clientId: string }) {
  const citations = useQuery(answerCitationsQuery(clientId));
  const data = citations.data;
  const questions = data ? [...data.questions].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status)) : [];
  return (
    <section aria-labelledby="answer-citations-heading" className="flex flex-col gap-3 rounded-card bg-card p-5 shadow-card lg:col-span-3">
      <div className="flex items-center justify-between gap-2">
        <h3 id="answer-citations-heading" className="text-body-medium text-foreground">
          Cited in AI answers
        </h3>
        <OriginChip origin="Web answer search" />
      </div>
      {citations.isPending ? <LoadingBlock rows={1} /> : null}
      {citations.error ? <ErrorState error={citations.error} onRetry={() => citations.refetch()} /> : null}
      {data && !data.counts.questions_checked ? <NotCheckedYet /> : null}
      {data && data.counts.questions_checked ? (
        <>
          <p className="text-body-regular text-muted-foreground">
            Your videos are cited for {data.counts.cite_you} of {plural(data.counts.questions_checked, "buyer question")}. A competitor is cited instead of you for{" "}
            {data.counts.competitor_only}.
          </p>
          <ul className="flex flex-col divide-y divide-border">
            {questions.map((question) => (
              <QuestionRow key={question.buyer_question_id} question={question} />
            ))}
          </ul>
          <CheckedLine data={data} />
          <CalcNote />
        </>
      ) : null}
    </section>
  );
}

export function CompetitorAnswerCitations({ clientId, names }: { clientId: string; names: Record<string, string> }) {
  const citations = useQuery(answerCitationsQuery(clientId));
  const data = citations.data;
  if (citations.isPending) return <LoadingBlock rows={1} />;
  if (citations.error) return <ErrorState error={citations.error} onRetry={() => citations.refetch()} />;
  if (!data) return null;
  const textOf = Object.fromEntries(data.questions.map((question) => [question.buyer_question_id, question.text]));
  const gaps = data.questions.filter((question) => question.status === "competitor_only");
  return (
    <section aria-labelledby="competitor-citations-heading" className="flex flex-col gap-3 rounded-card bg-card p-5 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <h2 id="competitor-citations-heading" className="text-body-medium text-foreground">
          Cited in AI answers
        </h2>
        <OriginChip origin="Web answer search" />
      </div>
      {!data.counts.questions_checked ? <NotCheckedYet /> : null}
      {data.counts.questions_checked && !data.competitors.length ? (
        <p className="text-body-regular text-muted-foreground">No competitor video was cited for the {plural(data.counts.questions_checked, "buyer question")} we checked.</p>
      ) : null}
      {data.competitors.length ? (
        <ul className="flex flex-col divide-y divide-border">
          {data.competitors.map((entry) => (
            <li key={entry.channel_id} className="flex flex-col gap-1 py-2.5">
              <p className="text-body-medium text-foreground">
                {names[entry.channel_id] ?? entry.channel_title ?? entry.channel_id}
                <span className="ml-2 text-caption-1-regular text-muted-foreground">
                  cited for {plural(entry.buyer_question_ids.length, "question")}
                </span>
              </p>
              <ul className="flex flex-col gap-0.5 text-caption-1-regular text-muted-foreground">
                {entry.buyer_question_ids.map((id) => (
                  <li key={id}>{textOf[id] ?? id}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : null}
      {gaps.length ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-caption-1-medium text-foreground">
            Gaps: a competitor is cited and you aren&apos;t · {gaps.length}
          </h3>
          <ul className="flex flex-col gap-0.5 text-caption-1-regular text-muted-foreground">
            {gaps.map((question) => (
              <li key={question.buyer_question_id}>{question.text}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {data.counts.questions_checked ? (
        <>
          <CheckedLine data={data} />
          <CalcNote />
        </>
      ) : null}
    </section>
  );
}
