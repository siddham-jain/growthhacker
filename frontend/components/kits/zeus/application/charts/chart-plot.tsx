// @zeus-internal
"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Measures the available plot in CSS pixels, independently of preview transforms. */
export function ChartPlot({
  children,
  roundDimensions = false,
}: {
  roundDimensions?: boolean;
  children: (size: { width: number; height: number }) => ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const width = roundDimensions ? Math.round(entry.contentRect.width) : entry.contentRect.width;
      const height = roundDimensions
        ? Math.round(entry.contentRect.height)
        : entry.contentRect.height;
      if (width > 0 && height > 0)
        setSize((previous) =>
          previous.width === width && previous.height === height ? previous : { width, height },
        );
    });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [roundDimensions]);
  return (
    <div ref={ref} className="relative min-h-0 w-full flex-1">
      {size.width > 0 && children(size)}
    </div>
  );
}
