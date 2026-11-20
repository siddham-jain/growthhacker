"use client";

import { useEffect, useRef, useState } from "react";
import { useHydratedReducedMotion } from "./use-hydrated-reduced-motion";

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

/**
 * Rolls the displayed number from its current value to `target` with an
 * ease-out curve. Used by the dashboard chart cards so headline figures
 * animate when the dataset or the hovered point changes.
 */
export function useCountUp(
  target: number,
  duration = 320,
  round = true,
  respectReducedMotion = true,
  initial?: { value: number; duration: number },
) {
  const prefersReduced = useHydratedReducedMotion();
  const reduced = respectReducedMotion && prefersReduced;
  const [display, setDisplay] = useState(initial?.value ?? target);
  const fromRef = useRef(initial?.value ?? target);
  const firstTarget = useRef<number | null>(initial ? target : null);

  useEffect(() => {
    const elapsedDuration = firstTarget.current === target && initial ? initial.duration : duration;
    if (firstTarget.current !== target) firstTarget.current = null;
    if (reduced || elapsedDuration <= 0) {
      fromRef.current = target;
      setDisplay(target);
      return;
    }
    const from = fromRef.current;
    if (from === target) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / elapsedDuration);
      const next = from + (target - from) * easeOutCubic(t);
      const value = round ? Math.round(next) : next;
      fromRef.current = value;
      setDisplay(value);
      if (t < 1) raf = requestAnimationFrame(tick);
      else {
        fromRef.current = target;
        firstTarget.current = null;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, reduced, round]);

  return reduced || duration <= 0 ? target : display;
}
