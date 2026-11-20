"use client";

import { type RefObject, useEffect, useLayoutEffect, useState } from "react";

const useIsomorphicLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

interface SelectionBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Keep selection chrome aligned when labels, fonts, items or container sizes change. */
export function useSelectionBounds(ref: RefObject<HTMLElement | null>, itemSelector: string) {
  const [bounds, setBounds] = useState<SelectionBounds | null>(null);
  useIsomorphicLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      const selected = element.querySelector<HTMLElement>(`${itemSelector}[data-selected]`);
      const next = selected
        ? {
            left: selected.offsetLeft,
            top: selected.offsetTop,
            width: selected.offsetWidth,
            height: selected.offsetHeight,
          }
        : null;
      setBounds((previous) =>
        previous?.left === next?.left &&
        previous?.top === next?.top &&
        previous?.width === next?.width &&
        previous?.height === next?.height
          ? previous
          : next,
      );
    };
    const observed = new Set<HTMLElement>();
    const resize = new ResizeObserver(measure);
    resize.observe(element);
    const observeItems = () => {
      const items = new Set(element.querySelectorAll<HTMLElement>(itemSelector));
      for (const item of observed) {
        if (!items.has(item)) {
          resize.unobserve(item);
          observed.delete(item);
        }
      }
      for (const item of items) {
        if (!observed.has(item)) {
          observed.add(item);
          resize.observe(item);
        }
      }
      measure();
    };
    observeItems();
    const mutations = new MutationObserver(observeItems);
    mutations.observe(element, {
      attributes: true,
      attributeFilter: ["data-selected"],
      childList: true,
      characterData: true,
      subtree: true,
    });
    return () => {
      mutations.disconnect();
      resize.disconnect();
    };
  }, [ref, itemSelector]);
  return bounds;
}
