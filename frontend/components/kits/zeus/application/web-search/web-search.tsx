"use client";

import {
  RiArrowDownSLine,
  RiLightbulbLine,
  RiSearchLine,
} from "@remixicon/react";
import { motion } from "motion/react";
import {
  type ComponentType,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  SOCIAL_PROVIDERS,
  type SocialProvider,
} from "../../base/social-button/social-providers";
import { cx } from "@/lib/cx";
import { Button } from "../../base/buttons/button";
import { Avatar } from "../../base/avatar/avatar";
import { AgentLogPreview } from "../agent-log/agent-log-preview";
import {
  GuideBridge,
  LogRow,
  ShimmerText,
  SOFT_EASE,
  UNIT_ANIMATE,
  UNIT_INITIAL,
  UNIT_TRANSITION,
  useLogMotion,
  useRevealMask,
  useRevealTicker,
  WorkingRow,
} from "../agent-log/agent-log";

export interface WebSearchSource {
  title: string;
  domain: string;
  brand?: SocialProvider;
  href?: string;
}
export interface WebSearchStep {
  id?: string;
  label: string;
  query?: string;
  meta?: string;
  brand?: SocialProvider;
  icon?: ComponentType<{
    className?: string;
    "aria-hidden"?: boolean | "true" | "false";
  }>;
  heading?: boolean;
  dwell?: number;
  /** Time spent reading the revealed source row before continuing. */
  sourceDwell?: number;
  sources?: WebSearchSource[];
}
export interface WebSearchProps {
  steps?: WebSearchStep[];
  revealed?: number;
  run?: boolean;
  startDelay?: number;
  stepInterval?: number;
  onComplete?: () => void;
  className?: string;
}
export const DEFAULT_WEB_SEARCH_STEPS: WebSearchStep[] = [
  { label: "Ran 3 searches", icon: RiSearchLine, heading: true, dwell: 1700 },
  {
    label: "Searching for what teams actually pay for in a component library",
    icon: RiLightbulbLine,
    meta: "5 results",
    dwell: 900,
    sourceDwell: 2400,
    sources: [
      {
        title: "A component library cost breakdown",
        domain: "research.example.com",
        href: "https://research.example.com/",
      },
      {
        title: "We shipped our design system in 6 weeks. Here is what it cost",
        domain: "teams.example.com",
        href: "https://teams.example.com/",
      },
      {
        title: "A component registry model explained",
        domain: "code.example.com",
        href: "https://code.example.com/",
      },
      {
        title: "Design tokens: a practical guide",
        domain: "design.example.com",
        href: "https://design.example.com/",
      },
      {
        title: "How we priced our component library",
        domain: "notes.example.com",
        href: "https://notes.example.com/",
      },
    ],
  },
  {
    label: "Searched public posts for",
    query: "component library OR design system pricing",
    meta: "7 posts",
    dwell: 1900,
  },
  {
    label: "Searched community threads for",
    query: "component APIs worth paying for",
    meta: "12 threads",
    dwell: 1800,
  },
  {
    label: "Reading the strongest three threads",
    icon: RiLightbulbLine,
    dwell: 2400,
  },
];

export function SearchSiteMark({
  brand,
  className,
}: {
  brand?: SocialProvider;
  className?: string;
}) {
  const provider = brand ? SOCIAL_PROVIDERS[brand] : undefined;
  if (!provider)
    return (
      <RiSearchLine
        aria-hidden
        className={cx("size-4 text-foreground-icon-secondary", className)}
      />
    );
  return (
    <svg
      aria-hidden
      viewBox={provider.viewBox}
      className={cx("size-4 shrink-0 text-text-primary", className)}
      style={{
        color:
          brand && ["github", "notion", "x"].includes(brand)
            ? undefined
            : provider.brand,
      }}
      fill="currentColor"
    >
      <path d={provider.path} />
    </svg>
  );
}
const SourceAvatar = motion.create(Avatar);

function Sources({ sources }: { sources: WebSearchSource[] }) {
  const [open, setOpen] = useState(false);
  const [expandedCount, setExpandedCount] = useState(0);
  const [stackCount, setStackCount] = useState(Math.min(5, sources.length));
  const expandedRef = useRef(0);
  const id = useId();
  const reduce = useLogMotion();
  const sourceCount = sources.length;

  useEffect(() => {
    const target = open ? sourceCount : 0;
    const current = Math.min(expandedRef.current, sourceCount);
    if (reduce) {
      expandedRef.current = target;
      setExpandedCount(target);
      setStackCount(Math.min(5, sourceCount - target));
      return;
    }
    expandedRef.current = current;
    setExpandedCount(current);
    const timers: number[] = [];
    const direction = target > current ? 1 : -1;
    for (let stage = 1; stage <= Math.abs(target - current); stage++) {
      const next = current + direction * stage;
      const delay = stage * 100;
      timers.push(
        window.setTimeout(() => {
          expandedRef.current = next;
          setExpandedCount(next);
        }, delay),
      );
      // The header gives the travelling icon room before its reserved slot resizes.
      timers.push(
        window.setTimeout(() => {
          setStackCount(Math.min(5, sourceCount - next));
        }, delay + 80),
      );
    }
    if (current === target) setStackCount(Math.min(5, sourceCount - target));
    return () => timers.forEach(window.clearTimeout);
  }, [open, reduce, sourceCount]);

  const sourceCircle = (source: WebSearchSource, index: number) => (
    <SourceAvatar
      size="xs"
      key={`${index}-${source.domain}`}
      layoutId={reduce ? undefined : `${id}-source-${index}`}
      initial={false}
      transition={
        reduce
          ? { duration: 0 }
          : { type: "spring", stiffness: 420, damping: 36 }
      }
      className="relative flex size-5 shrink-0 items-center justify-center overflow-visible rounded-full border border-border-button-default bg-background-primary-default text-body-regular"
      style={{ zIndex: sourceCount - index }}
    >
      <SearchSiteMark brand={source.brand} className="size-3" />
    </SourceAvatar>
  );
  return (
    <div className="py-1">
      <Button
        unstyled
        type="button"
        aria-label={`Open ${sources.length} sources`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
        className="group flex cursor-pointer items-center rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
      >
        <span className="text-body-regular text-text-secondary">Sources</span>
        <motion.span
          initial={false}
          animate={{ width: stackCount ? 20 + (stackCount - 1) * 14 : 0 }}
          transition={
            reduce
              ? { duration: 0 }
              : { type: "spring", stiffness: 420, damping: 36 }
          }
          className="ml-2 flex items-center"
        >
          <span className="flex items-center -space-x-1.5">
            {sources
              .slice(0, Math.min(5, sourceCount - expandedCount))
              .map(sourceCircle)}
          </span>
        </motion.span>
        <RiArrowDownSLine
          className={cx(
            "ml-1 size-4 shrink-0 text-foreground-icon-quaternary transition-transform duration-300 group-hover:text-foreground-icon-secondary motion-reduce:transition-none",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </Button>
      <div
        id={id}
        role="region"
        aria-label="Research sources"
        aria-hidden={!open}
        inert={!open}
      >
        <ul className="flex flex-col">
          {sources.map((source, index) => {
            const revealed = index >= sourceCount - expandedCount;
            return (
              <motion.li
                key={`${index}-${source.title}`}
                aria-hidden={!revealed}
                inert={!revealed}
                initial={false}
                animate={{ height: revealed ? "auto" : 0 }}
                transition={{ duration: reduce ? 0 : 0.28, ease: SOFT_EASE }}
                className="overflow-hidden"
              >
                <a
                  href={
                    source.href ??
                    `https://${source.domain.replace(/^https?:\/\//, "")}`
                  }
                  target="_blank"
                  rel="noreferrer"
                  className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 outline-none transition-colors duration-150 hover:bg-background-secondary-default focus-visible:ring-2 focus-visible:ring-border-focus-ring"
                >
                  {revealed ? (
                    sourceCircle(source, index)
                  ) : (
                    <span aria-hidden className="size-5 shrink-0" />
                  )}
                  <motion.span
                    initial={false}
                    animate={{ opacity: revealed ? 1 : 0 }}
                    transition={{
                      duration: reduce ? 0 : 0.16,
                      delay: reduce || !revealed ? 0 : 0.05,
                      ease: "easeOut",
                    }}
                    className="flex min-w-0 flex-1 items-center gap-2"
                  >
                    <span className="min-w-0 flex-1 truncate text-body-regular text-text-secondary">
                      {source.title}
                    </span>
                    <span className="hidden shrink-0 text-caption-1-regular text-text-tertiary sm:block">
                      {source.domain}
                    </span>
                  </motion.span>
                </a>
              </motion.li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function Heading({
  children,
  reduce,
}: {
  children: ReactNode;
  reduce: boolean;
}) {
  const mask = useRevealMask(reduce);
  return (
    <motion.div
      initial={reduce ? false : UNIT_INITIAL}
      animate={UNIT_ANIMATE}
      transition={UNIT_TRANSITION}
      {...mask}
      className="overflow-hidden"
    >
      {children}
    </motion.div>
  );
}

/** A search and its source row are separate reveal units, matching real events. */
export function WebSearch({
  steps = DEFAULT_WEB_SEARCH_STEPS,
  revealed: controlled,
  run = true,
  startDelay = 320,
  stepInterval = 850,
  onComplete,
  className,
}: WebSearchProps = {}) {
  const reduce = useLogMotion();
  const units = steps.flatMap((step, index) => [
    { step: index, sources: false },
    ...(step.sources?.length ? [{ step: index, sources: true }] : []),
  ]);
  // The final timed unit is the end of the last step's dwell. Controlled data
  // already represents completed events and does not need an extra unit.
  const total =
    units.length + (controlled === undefined && units.length > 0 ? 1 : 0);
  const revealed = useRevealTicker({
    total,
    run,
    startDelay,
    stepInterval,
    revealed: controlled,
    onComplete,
    delayFor: (index) =>
      index === 0
        ? startDelay
        : units[index - 1]?.sources
          ? (steps[units[index - 1]?.step ?? 0]?.sourceDwell ?? stepInterval)
          : (steps[units[index - 1]?.step ?? 0]?.dwell ?? stepInterval),
  });
  const complete = revealed >= total;
  let offset = 0;
  return (
    <div
      className={cx("flex w-full flex-col", className)}
      aria-label="Web research trail"
    >
      {steps.map((step, index) => {
        const start = offset;
        offset += 1 + (step.sources?.length ? 1 : 0);
        if (revealed <= start) return null;
        const active = !complete && revealed <= offset;
        const Icon = step.icon ?? RiSearchLine;
        const content = (
          <div className="flex items-start gap-2">
            {step.brand ? (
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
                <SearchSiteMark brand={step.brand} className="size-[13px]" />
              </span>
            ) : (
              <Icon
                aria-hidden
                className="mt-0.5 size-4 shrink-0 text-foreground-icon-secondary"
              />
            )}
            <p className="min-w-0 flex-1 text-body-regular text-text-secondary">
              {active ? <ShimmerText>{step.label}</ShimmerText> : step.label}
              {step.query && (
                <span className="ml-1.5 break-words font-mono text-caption-1-regular text-text-tertiary">
                  {step.query}
                </span>
              )}
            </p>
            {step.meta && (
              <span className="shrink-0 pt-px text-body-regular text-text-tertiary tabular-nums">
                {step.meta}
              </span>
            )}
          </div>
        );
        if (step.heading)
          return (
            <Heading key={step.id ?? index} reduce={reduce}>
              <div className="py-1">{content}</div>
            </Heading>
          );
        const last = index === steps.length - 1 || revealed <= offset;
        return (
          <div key={step.id ?? index} className="relative ml-[7px]">
            {index > 0 && steps[index - 1]?.heading && (
              <GuideBridge height={6} offset={0} reduce={reduce} />
            )}
            <ul>
              <LogRow
                first={index === 0}
                last={last}
                reduce={reduce}
                className="pl-[14px]"
              >
                <div className="py-1">
                  {content}
                  {step.sources?.length && revealed > start + 1 ? (
                    <div className="relative mt-0.5">
                      <GuideBridge height={5} offset={7} reduce={reduce} />
                      <ul className="ml-[7px]">
                        <LogRow first={false} last reduce={reduce}>
                          <Sources sources={step.sources} />
                        </LogRow>
                      </ul>
                    </div>
                  ) : null}
                </div>
              </LogRow>
            </ul>
          </div>
        );
      })}
      {!complete && <WorkingRow label="Working" reduce={reduce} />}
    </div>
  );
}

/** A replayable demonstration of the reusable research trail. */
export function WebSearchPreview(props: WebSearchProps = {}) {
  return (
    <AgentLogPreview
      maxWidth={560}
      minHeight={420}
      renderLog={(complete) => (
        <WebSearch
          {...props}
          onComplete={() => {
            props.onComplete?.();
            complete();
          }}
        />
      )}
    />
  );
}
