"use client";

import { Select, SelectItem } from "@/components/ui/primitives";

const SORT_LABELS: Record<string, string> = {
  matching_uploads: "Videos on your topics",
  median_outlier_ratio: "Median vs their normal",
  median_views_at_age: "Median views at day 7",
  last_upload_at: "Most recent upload",
  uploads_90d: "Uploads in 90 days",
};

export default function CreatorSort({ sorts, selected, onChange }: { sorts: string[]; selected: string; onChange: (sort: string) => void }) {
  return (
    <Select label="Sort within each group by" selectedKey={selected} onSelectionChange={(key) => key && onChange(String(key))} className="w-64">
      {sorts.map((id) => (
        <SelectItem key={id} id={id} textValue={SORT_LABELS[id] ?? id}>
          {SORT_LABELS[id] ?? id.replace(/_/g, " ")}
        </SelectItem>
      ))}
    </Select>
  );
}
