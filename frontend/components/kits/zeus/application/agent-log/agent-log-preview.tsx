"use client";
import { RiRefreshLine } from "@remixicon/react";
import { Fragment, type ReactNode, useState } from "react";
import { Button } from "../../base/buttons/button";

/** Shared log canvas and replay control; applications may use the raw logs directly. */
export function AgentLogPreview({
  maxWidth,
  minHeight,
  renderLog,
}: {
  maxWidth: number;
  minHeight: number;
  renderLog: (onComplete: () => void) => ReactNode;
}) {
  const [turn, setTurn] = useState(0);
  const [complete, setComplete] = useState(false);
  return (
    <div
      className="flex w-full items-center justify-center p-2"
      style={{ minHeight }}
    >
      <div className="flex w-full flex-col gap-4" style={{ maxWidth }}>
        <Fragment key={turn}>{renderLog(() => setComplete(true))}</Fragment>
        {complete && (
          <Button
            unstyled
            className="flex cursor-pointer items-center gap-1.5 self-start rounded-lg border border-border-button-default bg-background-primary-default px-2.5 py-1.5 text-body-medium text-text-secondary shadow-xs transition-colors duration-150 hover:bg-background-primary-hover hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring focus-visible:outline-none"
            onClick={() => {
              setComplete(false);
              setTurn((value) => value + 1);
            }}
          >
            <RiRefreshLine className="size-4" aria-hidden />
            Run again
          </Button>
        )}
      </div>
    </div>
  );
}
