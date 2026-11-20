import type { InsertionOp } from "@/lib/api/types";

export type DiffRow =
  | { kind: "context"; text: string }
  | { kind: "insert"; text: string; op: InsertionOp | null }
  | { kind: "link_fix"; oldText: string; text: string; op: InsertionOp | null }
  | { kind: "gap"; lines: number };

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@/;

function matchOp(text: string, operations: InsertionOp[], kind: InsertionOp["kind"]): InsertionOp | null {
  return operations.find((op) => op.kind === kind && text.includes(op.text.trim())) ?? null;
}

// reads the backend's preview_diff (a difflib unified diff with 3 lines of context); lines outside the hunks become gaps
export function parseUnifiedDiff(diff: string, operations: InsertionOp[]): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldLine = 1;
  const lines = diff.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith("---") || line.startsWith("+++")) continue;
    const hunk = HUNK.exec(line);
    if (hunk) {
      const start = Number(hunk[1]);
      if (start > oldLine) rows.push({ kind: "gap", lines: start - oldLine });
      oldLine = start;
      continue;
    }
    const tag = line.charAt(0);
    const text = line.slice(1);
    if (tag === " ") {
      rows.push({ kind: "context", text });
      oldLine += 1;
    } else if (tag === "+") {
      rows.push({ kind: "insert", text, op: matchOp(text, operations, "insert") });
    } else if (tag === "-") {
      // the only non-additive change 09 makes is a link fix: one removed line replaced by the next added one
      const next = lines[index + 1];
      if (next?.startsWith("+")) {
        rows.push({ kind: "link_fix", oldText: text, text: next.slice(1), op: matchOp(next.slice(1), operations, "link_fix") });
        index += 1;
      } else rows.push({ kind: "link_fix", oldText: text, text: "", op: null });
      oldLine += 1;
    }
  }
  return rows;
}

export function addedText(rows: DiffRow[]): string {
  return rows
    .filter((row): row is Extract<DiffRow, { kind: "insert" | "link_fix" }> => row.kind === "insert" || row.kind === "link_fix")
    .map((row) => row.text)
    .filter(Boolean)
    .join("\n");
}

export function highlightKeywords(text: string, keywords: string[]): { text: string; mark: boolean }[] {
  const terms = keywords.filter(Boolean).map((keyword) => keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!terms.length) return [{ text, mark: false }];
  const pattern = new RegExp(`(${terms.join("|")})`, "gi");
  return text
    .split(pattern)
    .filter(Boolean)
    .map((part) => ({ text: part, mark: terms.some((term) => new RegExp(`^${term}$`, "i").test(part)) }));
}
