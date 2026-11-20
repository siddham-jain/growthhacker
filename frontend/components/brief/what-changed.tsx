import type { ReactNode } from "react";
import { OriginChip } from "@/components/ui/chips";

interface TileProps {
  origin: "YouTube public" | "News";
  label: string;
  value: ReactNode;
  comparison: string;
}

// each tile is single-source; nothing here blends youtube numbers with other origins
export function ChangeTile({ origin, label, value, comparison }: TileProps) {
  return (
    <div className="flex flex-col gap-2 rounded-card bg-card px-[18px] py-4 shadow-card">
      <span className="w-fit">
        <OriginChip origin={origin} />
      </span>
      <p className="text-body-regular text-muted-foreground">{label}</p>
      <p className="text-metric text-foreground">{value}</p>
      <p className="text-caption-1-regular text-muted-foreground">{comparison}</p>
    </div>
  );
}
