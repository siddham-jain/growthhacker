"use client";

import { createContext, useContext, useRef } from "react";
import type { KeyboardEvent, ReactNode, Ref } from "react";
import { cx } from "@/lib/cx";
import { useSelectionBounds } from "@/hooks/use-selection-bounds";

/**
 * Single-select segmented control.
 *   track    bg background/tertiary/default, p 4, radius 10 (radius/2lg), gap 2
 *   segment  px 10, py 4, radius/md (6px)
 *     selected    bg segmented-control/selected (white in light), shadow/2xs, Body 1/Medium,
 *                 text/primary
 *     unselected  transparent, Body 1/Regular, text/secondary
 *
 * Fork: a native radio group instead of react-aria's ToggleButtonGroup, so the page header that
 * every screen shows doesn't load react-aria. Arrow keys move and select, like radios.
 */

export type SegmentedControlVariant = "solid" | "plain";

type Key = string | number;

interface GroupState {
  selected: Key | undefined;
  select: (key: Key) => void;
}

const GroupContext = createContext<GroupState | null>(null);

export interface SegmentedControlProps {
  children?: ReactNode;
  variant?: SegmentedControlVariant;
  selectedKeys?: Iterable<Key>;
  onSelectionChange?: (keys: Set<Key>) => void;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  ref?: Ref<HTMLDivElement>;
}

function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
  if (!step) return;
  const radios = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button[role=radio]:not(:disabled)")];
  const index = radios.indexOf(document.activeElement as HTMLButtonElement);
  const next = radios[(index + step + radios.length) % radios.length];
  if (!next) return;
  event.preventDefault();
  next.focus();
  next.click();
}

export function SegmentedControl({ className, children, variant = "solid", selectedKeys, onSelectionChange, ref, ...aria }: SegmentedControlProps) {
  const innerRef = useRef<HTMLDivElement>(null);
  const thumb = useSelectionBounds(innerRef, "button");
  const [selected] = selectedKeys ? [...selectedKeys] : [];

  const setRefs = (node: HTMLDivElement | null) => {
    innerRef.current = node;
    if (typeof ref === "function") ref(node);
    else if (ref) (ref as { current: HTMLDivElement | null }).current = node;
  };

  return (
    <GroupContext.Provider value={{ selected, select: (key) => key !== selected && onSelectionChange?.(new Set([key])) }}>
      <div ref={setRefs} role="radiogroup" onKeyDown={moveFocus} {...aria} className={cx("relative inline-flex items-start gap-0.5 rounded-2lg", variant === "solid" && "bg-segmented-control-background p-1", className)}>
        {variant === "solid" && thumb && (
          <span
            aria-hidden
            className="pointer-events-none absolute left-0 top-0 rounded-md bg-segmented-control-selected-background shadow-2xs transition-[transform,width,height] duration-200 ease"
            style={{ transform: `translate(${thumb.left}px, ${thumb.top}px)`, width: thumb.width, height: thumb.height }}
          />
        )}
        {children}
      </div>
    </GroupContext.Provider>
  );
}

export interface SegmentedControlItemProps {
  id: Key;
  children?: ReactNode;
  isDisabled?: boolean;
  className?: string;
  ref?: Ref<HTMLButtonElement>;
}

export function SegmentedControlItem({ id, className, children, isDisabled, ref }: SegmentedControlItemProps) {
  const group = useContext(GroupContext);
  const isSelected = group?.selected !== undefined && String(group.selected) === String(id);
  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={isSelected}
      // selection-bounds measures the selected segment through this attribute
      data-selected={isSelected || undefined}
      tabIndex={isSelected ? 0 : -1}
      disabled={isDisabled}
      onClick={() => group?.select(id)}
      className={cx(
        "relative z-10 inline-flex cursor-pointer items-center justify-center rounded-md px-2.5 py-1 text-center whitespace-nowrap",
        "transition-colors duration-200 ease",
        "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        isSelected ? "text-body-medium text-text-primary" : "text-body-regular text-text-secondary hover:text-text-primary",
        isDisabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      {children}
    </button>
  );
}
