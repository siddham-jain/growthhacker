import { cx } from "@/lib/cx";
import { CALC_MARK, METRIC_DISCLOSURE } from "@/lib/labels";

// screen readers get the disclosure once per section from CalcNote, so the inline mark is visual only
export function CalcMark({ className }: { className?: string }) {
  return (
    <sup aria-hidden title={METRIC_DISCLOSURE} className={cx("ml-px cursor-help align-super text-[0.65em] font-medium text-subtle-foreground", className)}>
      {CALC_MARK}
    </sup>
  );
}

export function CalcNote({ className }: { className?: string }) {
  return (
    <p className={cx("text-caption-1-regular text-subtle-foreground", className)}>
      <span aria-hidden>{CALC_MARK} </span>
      {METRIC_DISCLOSURE}
    </p>
  );
}
