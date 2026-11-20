import type { Confidence } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { CalcMark } from "./calc-mark";

const BARS: Record<Confidence["label"], number> = { High: 3, Medium: 2, Low: 1 };

// three-bar signal meter adapted from beautiful ui's recommendation card; the text label is always shown
export function ConfidenceMeter({ confidence, firstMover = false, compact = false }: { confidence: Confidence; firstMover?: boolean; compact?: boolean }) {
  const filled = firstMover ? 0 : BARS[confidence.label];
  const label = firstMover ? "First-mover" : confidence.label;
  return (
    <span className="inline-flex items-center gap-2 text-caption-1-medium text-muted-foreground">
      <span aria-hidden className="flex items-end gap-0.5">
        {[0, 1, 2].map((bar) => (
          <span key={bar} className={cx("h-2.5 w-1 rounded-full", bar < filled ? "bg-foreground" : "bg-border-strong")} />
        ))}
      </span>
      <span>
        <span className="text-foreground">{label}</span>
        {compact ? null : firstMover ? " · no reference videos yet" : ` · ${confidence.outliers} outliers · ${confidence.channels} channels`}
        {compact || firstMover ? null : <CalcMark />}
      </span>
    </span>
  );
}
