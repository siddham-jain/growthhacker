"use client";

import { RiBarChartBoxLine, RiTableLine } from "@remixicon/react";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

// every chart has the same numbers available as a plain table
export function ChartTableToggle({ chart, table, label }: { chart: ReactNode; table: ReactNode; label: string }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex justify-end">
        <Button variant="ghost" size="xs" leadingIcon={asTable ? RiBarChartBoxLine : RiTableLine} aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? "View as chart" : "View as table"}
          <span className="sr-only"> for {label}</span>
        </Button>
      </div>
      {asTable ? <div className="max-h-64 overflow-auto">{table}</div> : chart}
    </div>
  );
}

export function DataTable({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  return (
    <table className="w-full text-left text-caption-1-regular tabular-nums">
      <thead className="sticky top-0 bg-card">
        <tr>
          {columns.map((column) => (
            <th key={column} scope="col" className="border-b border-border px-2 py-1.5 font-medium text-muted-foreground">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={index} className="border-b border-border last:border-0">
            {row.map((cell, cellIndex) => (
              <td key={cellIndex} className="px-2 py-1.5 text-foreground">
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
