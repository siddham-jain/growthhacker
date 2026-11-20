"use client";

import { createContext, useContext, useId, useRef } from "react";
import type { ComponentType, KeyboardEvent, ReactNode, Ref } from "react";
import { cx } from "@/lib/cx";
import { useSelectionBounds } from "@/hooks/use-selection-bounds";

/**
 * Underline tabs. The tab strip sits on a 1px border/button/default baseline;
 * the active tab paints a 2px color/blue/600 underline over it.
 *   tab       px 10, py 8, gap 10 (label ↔ count), Body 1 (14/20)
 *     active     text color/blue/600, Medium weight, 2px blue underline
 *     inactive   text/primary, Regular weight, transparent underline
 *   count     radius/sm, px 4, py 1, Caption 1/Medium (12/16)
 *     active     bg color/blue/100, text color/blue/600
 *     inactive   bg black/10 @ 50% opacity, text/primary
 *   icon      optional 16px leading glyph, inherits the label color
 *
 * Fork: native ARIA tabs instead of react-aria's, so screens with tabs don't load react-aria.
 * Controlled only; arrow keys, Home and End move between tabs with automatic activation.
 */

type Key = string | number;

type IconComponent = ComponentType<{
  className?: string;
  "aria-hidden"?: boolean | "true" | "false";
}>;

interface TabsState {
  selected: Key;
  select: (key: Key) => void;
  idFor: (kind: "tab" | "panel", key: Key) => string;
}

const TabsContext = createContext<TabsState | null>(null);

function useTabs(): TabsState {
  const state = useContext(TabsContext);
  if (!state) throw new Error("Tab, TabList and TabPanel must sit inside Tabs");
  return state;
}

export interface TabsProps {
  selectedKey: Key;
  onSelectionChange: (key: Key) => void;
  children?: ReactNode;
  className?: string;
  ref?: Ref<HTMLDivElement>;
}

export function Tabs({ selectedKey, onSelectionChange, className, children, ref }: TabsProps) {
  const base = useId();
  const idFor = (kind: "tab" | "panel", key: Key) => `${base}-${kind}-${String(key).replace(/\W/g, "_")}`;
  return (
    <TabsContext.Provider value={{ selected: selectedKey, select: (key) => key !== selectedKey && onSelectionChange(key), idFor }}>
      <div ref={ref} className={cx("flex w-full min-w-0 flex-col gap-4", className)}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')];
  const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
  const targets: Record<string, number> = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 };
  if (!(event.key in targets) || index < 0) return;
  event.preventDefault();
  const next = tabs[(targets[event.key] + tabs.length) % tabs.length];
  next.focus();
  next.click();
}

export interface TabListProps {
  /** Include the baseline border beneath the tab strip. */
  bordered?: boolean;
  /** Keep horizontal overflow in the strip; disable inside an existing scroll area. */
  scrollable?: boolean;
  children?: ReactNode;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLDivElement>;
}

export function TabList({ className, ref, bordered = true, scrollable = true, children, ...aria }: TabListProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const underline = useSelectionBounds(wrapperRef, '[role="tab"]');

  return (
    <div className="w-full min-w-0">
      <div className={scrollable ? "-m-0.5 overflow-x-auto scroll-px-0.5 p-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" : undefined}>
        <div ref={wrapperRef} className={cx("relative", scrollable ? "w-max min-w-full" : "w-full")}>
          <div ref={ref} role="tablist" onKeyDown={moveFocus} {...aria} className={cx("flex w-full items-center gap-1 border-separator-border", bordered ? "border-b" : "border-b-0", className)}>
            {children}
          </div>
          {underline && (
            <span
              aria-hidden
              className={cx("pointer-events-none absolute left-0 h-0.5 bg-accent-600 transition-[transform,width] duration-200 ease", bordered ? "top-0" : "bottom-0")}
              style={{
                transform: bordered ? `translate(${underline.left}px, ${underline.top + underline.height - 1}px)` : `translateX(${underline.left}px)`,
                width: underline.width,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export interface TabProps {
  id: Key;
  children?: ReactNode;
  /** Optional leading icon (16px). Inherits the label color. */
  icon?: IconComponent;
  /** Optional trailing count badge. */
  count?: ReactNode;
  isDisabled?: boolean;
  className?: string;
  ref?: Ref<HTMLButtonElement>;
}

export function Tab({ id, className, children, icon: Icon, count, isDisabled, ref }: TabProps) {
  const tabs = useTabs();
  const isSelected = tabs.selected === id;
  return (
    <button
      ref={ref}
      type="button"
      role="tab"
      id={tabs.idFor("tab", id)}
      aria-selected={isSelected}
      aria-controls={isSelected ? tabs.idFor("panel", id) : undefined}
      // selection-bounds measures the selected tab through this attribute
      data-selected={isSelected || undefined}
      tabIndex={isSelected ? 0 : -1}
      disabled={isDisabled}
      onClick={() => tabs.select(id)}
      className={cx(
        "relative inline-flex cursor-pointer items-center gap-2.5 px-2.5 py-2 whitespace-nowrap",
        "outline-none transition-colors duration-150 ease",
        "focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        isDisabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      <span className={cx("inline-flex items-center gap-1.5", isSelected ? "text-body-medium text-accent-600" : "text-body-regular text-text-primary")}>
        {Icon && <Icon className="size-4 shrink-0" aria-hidden />}
        {children}
      </span>
      {count != null && (
        <span className={cx("inline-flex items-center justify-center rounded-sm px-1 py-px text-caption-1-medium whitespace-nowrap", isSelected ? "bg-tab-count-selected-background text-accent-600" : "bg-black/10 text-text-primary opacity-50")}>
          {count}
        </span>
      )}
    </button>
  );
}

export interface TabPanelProps {
  id: Key;
  children?: ReactNode;
  className?: string;
  ref?: Ref<HTMLDivElement>;
}

// only the selected panel renders, as with react-aria
export function TabPanel({ id, className, children, ref }: TabPanelProps) {
  const tabs = useTabs();
  if (tabs.selected !== id) return null;
  return (
    <div ref={ref} role="tabpanel" id={tabs.idFor("panel", id)} aria-labelledby={tabs.idFor("tab", id)} tabIndex={0} className={cx("outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-border-focus-ring", className)}>
      {children}
    </div>
  );
}
