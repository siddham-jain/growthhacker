"use client";

import {
  RiArrowDownSLine,
  RiFileCodeLine,
  RiSearchLine,
} from "@remixicon/react";
import { AnimatePresence, motion } from "motion/react";
import { type ComponentType, type ReactNode, useId, useState } from "react";
import { cx } from "@/lib/cx";
import { Button } from "../../base/buttons/button";
import { Chip } from "../../base/badges/chip";
import { AgentLogPreview } from "../agent-log/agent-log-preview";
import {
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

export interface TaskResourceChip {
  label: string;
  icon?: ReactNode;
  href?: string;
}
export interface AgentTaskStep {
  label: string;
  chips?: TaskResourceChip[];
}
export interface AgentTask {
  id?: string;
  label: string;
  completedLabel?: string;
  icon?: ComponentType<{
    className?: string;
    "aria-hidden"?: boolean | "true" | "false";
  }>;
  steps?: AgentTaskStep[];
}
export interface TaskListProps {
  tasks?: AgentTask[];
  revealed?: number;
  run?: boolean;
  startDelay?: number;
  stepInterval?: number;
  collapseOnComplete?: boolean | "all";
  onComplete?: () => void;
  className?: string;
}
const fileIcon = (
  <RiFileCodeLine className="size-3.5 text-foreground-icon-secondary" />
);
export const DEFAULT_AGENT_TASKS: AgentTask[] = [
  {
    id: "search",
    label: "Searching project files",
    completedLabel: "Found project files",
    icon: RiSearchLine,
    steps: [
      { label: 'Searching "app/page.tsx, components structure"' },
      { label: "Read", chips: [{ label: "page.tsx", icon: fileIcon }] },
      { label: "Scanning 52 files" },
      {
        label: "Reading files",
        chips: [{ label: "layout.tsx", icon: fileIcon }],
      },
    ],
  },
  {
    id: "edit",
    label: "Editing files",
    completedLabel: "Registered the theme toggle",
    icon: RiFileCodeLine,
    steps: [
      {
        label: "Created",
        chips: [{ label: "theme-toggle.tsx", icon: fileIcon }],
      },
      {
        label: "Wired it into",
        chips: [{ label: "layout.tsx", icon: fileIcon }],
      },
      { label: "Ran lint and type-check, 0 errors" },
    ],
  },
];

export function TaskChip({ label, icon, href }: TaskResourceChip) {
  const classes =
    "mr-0.5 ml-[3px] inline-flex max-w-full -translate-y-px items-center gap-1 rounded-md border border-border-button-default/50 bg-background-secondary-default py-0.5 pr-1.5 pl-1 align-middle transition-colors duration-150 hover:border-border-button-hover hover:bg-background-tertiary-default";
  const content = (
    <>
      {icon}
      <span className="truncate text-caption-2-medium text-text-primary">
        {label}
      </span>
    </>
  );
  return href ? (
    <a
      href={href}
      className={cx(
        classes,
        "outline-none hover:bg-background-secondary-hover focus-visible:ring-2 focus-visible:ring-border-focus-ring",
      )}
    >
      {content}
    </a>
  ) : (
    <Chip variant="resource" color="soft" className={classes}>
      {content}
    </Chip>
  );
}
function TaskGroup({
  task,
  shown,
  done,
  collapse,
  reduce,
}: {
  task: AgentTask;
  shown: number;
  done: boolean;
  collapse: boolean;
  reduce: boolean;
}) {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  const open = manualOpen ?? !collapse;
  const id = useId();
  const mask = useRevealMask(reduce);
  const steps = task.steps ?? [];
  const Icon = task.icon ?? RiSearchLine;
  const label = done ? (task.completedLabel ?? task.label) : task.label;
  return (
    <motion.section
      initial={reduce ? false : UNIT_INITIAL}
      animate={UNIT_ANIMATE}
      transition={UNIT_TRANSITION}
      {...mask}
      className="overflow-hidden"
    >
      <Button
        unstyled
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setManualOpen(!open)}
        className="group flex w-full cursor-pointer items-center gap-2 rounded-md py-0.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
      >
        <Icon
          aria-hidden
          className="size-4 shrink-0 text-foreground-icon-secondary"
        />
        <span className="min-w-0 flex-1 text-body-medium text-text-secondary">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={label}
              initial={
                reduce ? false : { opacity: 0, y: 3, filter: "blur(3px)" }
              }
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -3, filter: "blur(3px)" }}
              transition={{ duration: reduce ? 0 : 0.3, ease: SOFT_EASE }}
              className="block truncate"
            >
              {done ? label : <ShimmerText>{label}</ShimmerText>}
            </motion.span>
          </AnimatePresence>
        </span>
        <RiArrowDownSLine
          aria-hidden
          className={cx(
            "size-4 shrink-0 text-foreground-icon-tertiary transition-transform duration-300 group-hover:text-foreground-icon-secondary motion-reduce:transition-none",
            open && "rotate-180",
          )}
        />
      </Button>
      <motion.div
        id={id}
        aria-hidden={!open}
        inert={!open}
        initial={false}
        animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
        transition={{
          height: { duration: reduce ? 0 : 0.3, ease: SOFT_EASE },
          opacity: { duration: reduce ? 0 : 0.22, ease: "easeOut" },
        }}
        className="overflow-hidden"
      >
        <ul className="mt-0.5 ml-2 flex flex-col">
          {steps.slice(0, shown).map((step, index) => (
            <LogRow
              key={`${index}-${step.label}`}
              first={index === 0}
              last={index === Math.min(steps.length, shown) - 1}
              reduce={reduce}
            >
              <span className="block py-1 text-body-regular text-text-secondary">
                {step.label}
                {step.chips?.map((chip, index) => (
                  <TaskChip key={`${index}-${chip.label}`} {...chip} />
                ))}
              </span>
            </LogRow>
          ))}
        </ul>
      </motion.div>
    </motion.section>
  );
}

/** `revealed` counts one unit for each task header and each child step. */
export function TaskList({
  tasks = DEFAULT_AGENT_TASKS,
  revealed: controlled,
  run = true,
  startDelay = 320,
  stepInterval = 850,
  collapseOnComplete = false,
  onComplete,
  className,
}: TaskListProps = {}) {
  const reduce = useLogMotion();
  const total = tasks.reduce(
    (sum, task) => sum + 1 + (task.steps?.length ?? 0),
    0,
  );
  const timedTotal = total + (controlled === undefined && total > 0 ? 1 : 0);
  const revealed = useRevealTicker({
    total: timedTotal,
    run,
    startDelay,
    stepInterval,
    revealed: controlled,
    onComplete,
  });
  const complete = revealed >= timedTotal;
  let offset = 0;
  return (
    <div
      className={cx("flex w-full flex-col gap-[5px]", className)}
      aria-label="Agent task log"
    >
      {tasks.map((task, index) => {
        const start = offset;
        const units = 1 + (task.steps?.length ?? 0);
        offset += units;
        if (revealed <= start) return null;
        const done = revealed >= start + units;
        return (
          <TaskGroup
            key={task.id ?? `${index}-${task.label}`}
            task={task}
            shown={Math.max(0, revealed - start - 1)}
            done={done}
            reduce={reduce}
            collapse={
              collapseOnComplete === "all"
                ? complete
                : collapseOnComplete && done
            }
          />
        );
      })}
      {!complete && <WorkingRow label="Working" reduce={reduce} />}
    </div>
  );
}

/** A replayable demonstration built from the same task log used by applications. */
export function TaskListPreview(props: TaskListProps = {}) {
  return (
    <AgentLogPreview
      maxWidth={420}
      minHeight={320}
      renderLog={(complete) => (
        <TaskList
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
