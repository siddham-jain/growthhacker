import { cx } from "@/lib/cx";
import { METRIC_DISCLOSURE } from "@/lib/labels";

// one disclosure per section instead of a mark on every computed number
export function CalcNote({ className }: { className?: string }) {
  return <p className={cx("text-caption-1-regular text-subtle-foreground", className)}>{METRIC_DISCLOSURE}</p>;
}
