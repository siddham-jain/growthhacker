"use client";

import type { Key } from "react-aria-components";
import { SegmentedControl, SegmentedControlItem } from "@/components/ui/primitives";
import type { LensFilter, WindowDays } from "@/lib/api/types";
import { useViewParams } from "@/lib/url-state";

function firstKey(keys: Set<Key>): string | undefined {
  const [value] = [...keys];
  return value === undefined ? undefined : String(value);
}

export function ViewControls({ showLens = true }: { showLens?: boolean }) {
  const { windowDays, lens, setWindow, setLens } = useViewParams();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SegmentedControl
        aria-label="How far back we look"
        selectedKeys={[String(windowDays)]}
        onSelectionChange={(keys) => {
          const value = firstKey(keys);
          if (value) setWindow(Number(value) as WindowDays);
        }}
      >
        {[7, 30, 90].map((days) => (
          <SegmentedControlItem key={days} id={String(days)}>
            {days}d
          </SegmentedControlItem>
        ))}
      </SegmentedControl>
      {showLens ? (
        <SegmentedControl
          aria-label="Lens"
          selectedKeys={[lens]}
          onSelectionChange={(keys) => {
            const value = firstKey(keys);
            if (value) setLens(value as LensFilter);
          }}
        >
          <SegmentedControlItem id="views">Views</SegmentedControlItem>
          <SegmentedControlItem id="conversion">Conversion</SegmentedControlItem>
          <SegmentedControlItem id="both">Both</SegmentedControlItem>
        </SegmentedControl>
      ) : null}
    </div>
  );
}
