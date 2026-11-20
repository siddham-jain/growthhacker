"use client";
/**
 * Shimmer: a loading surface with a slanted beam of light that travels left
 * to right. The beam is three soft ellipses stacked like a border glow (a wide
 * bloom, the beam, a bright core), each drifting a little on its own, over an
 * optional film grain. Four presets; every value is a prop.
 */
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

type Rgb = string;
interface ShimmerLook {
  base: string;
  /** Bloom, beam and core, as space-separated RGB. */
  light: [Rgb, Rgb, Rgb];
}

export const SHIMMER_PRESETS = {
  paper: { light: { base: "#f2f2f2", light: ["255 255 255", "255 255 255", "255 255 255"] }, dark: { base: "#222224", light: ["92 92 98", "92 92 98", "110 110 118"] } },
  opal: { light: { base: "#eef0f6", light: ["255 214 236", "214 230 255", "255 255 255"] }, dark: { base: "#1f2028", light: ["120 92 120", "88 104 140", "140 140 160"] } },
  clay: { light: { base: "#ede5dc", light: ["255 228 204", "255 242 228", "255 252 246"] }, dark: { base: "#2a2420", light: ["122 96 80", "120 104 92", "150 132 118"] } },
  frost: { light: { base: "#e7eef6", light: ["206 230 255", "232 244 255", "255 255 255"] }, dark: { base: "#1c2128", light: ["74 102 132", "92 116 140", "128 150 172"] } },
} satisfies Record<string, { light: ShimmerLook; dark: ShimmerLook }>;

export type ShimmerPreset = keyof typeof SHIMMER_PRESETS;

const GRAIN =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23g)'/%3E%3C/svg%3E\")";

export interface ShimmerProps {
  preset?: ShimmerPreset;
  /** Use the preset's dark pair; "auto" follows the page's color-scheme. */
  dark?: boolean | "auto";
  /** Surface colour; overrides the preset. */
  base?: string;
  /** Light colour, as one CSS colour or three (bloom, beam, core) space-separated RGB triples. */
  highlight?: string | [string, string, string];
  /** Milliseconds for one pass. */
  speed?: number;
  /** Beam width as a percentage of the surface. */
  width?: number;
  /** How soft the beam's edges are, 0–100. */
  softness?: number;
  /** Lean of the beam in degrees; negative leans right. */
  slant?: number;
  /** Brightness of the beam, 0–100. */
  intensity?: number;
  /** Film grain over the surface, 0–100. */
  grain?: number;
  playing?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

const layer: CSSProperties = { position: "absolute", inset: 0 };

export function Shimmer({
  preset = "paper",
  dark = false,
  base,
  highlight,
  speed = 1550,
  width = 38,
  softness = 58,
  slant = -18,
  intensity = 78,
  grain = 7,
  playing = true,
  className,
  style,
  children,
}: ShimmerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const bloomRef = useRef<HTMLDivElement>(null);
  const beamRef = useRef<HTMLDivElement>(null);
  const coreRef = useRef<HTMLDivElement>(null);

  const pair = SHIMMER_PRESETS[preset];
  const look = pair[dark === true ? "dark" : "light"];
  const light: [string, string, string] = Array.isArray(highlight) ? highlight : highlight ? [highlight, highlight, highlight] : look.light;
  const mix = (c: string, a: number) => (/^\d/.test(c) ? `rgb(${c} / ${a})` : `color-mix(in srgb, ${c} ${Math.round(a * 100)}%, transparent)`);
  // With "auto", each colour is a light-dark() pair resolved by the page's color-scheme.
  const auto = dark === "auto" && !highlight;
  const tone = (c: string, a: number, i: number) => (auto ? `light-dark(${mix(c, a)}, ${mix(pair.dark.light[i], a)})` : mix(c, a));
  const surface = base ?? (dark === "auto" ? `light-dark(${pair.light.base}, ${pair.dark.base})` : look.base);
  const k = intensity / 78;
  const soft = softness / 58;

  useEffect(() => {
    const host = hostRef.current;
    const track = trackRef.current;
    if (!host || !track || !track.animate) return;
    if (!playing || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      track.style.transform = `translateX(-200%) skewX(${slant}deg)`;
      return;
    }
    let runs: Animation[] = [];
    const start = () => {
      runs.forEach((r) => r.cancel());
      const w = host.offsetWidth;
      const t = track.offsetWidth;
      const ease = "ease-in-out";
      runs = [
        track.animate(
          [{ transform: `translateX(${-t * 1.6}px) skewX(${slant}deg)` }, { transform: `translateX(${w + t * 0.6}px) skewX(${slant}deg)` }],
          { duration: speed, iterations: Infinity, easing: ease },
        ),
      ];
      const drift = (el: HTMLElement | null, frames: Keyframe[], factor: number) => {
        if (el) runs.push(el.animate(frames, { duration: speed * factor, iterations: Infinity, easing: ease }));
      };
      drift(bloomRef.current, [{ transform: "translate3d(-7%, 4%, 0) scale(1.02)" }, { transform: "translate3d(0, -6%, 0) scale(1.12)" }, { transform: "translate3d(-7%, 4%, 0) scale(1.02)" }], 2.17);
      drift(beamRef.current, [{ transform: "translate3d(3%, -5%, 0)" }, { transform: "translate3d(-5%, 6%, 0)", offset: 0.4 }, { transform: "translate3d(2%, 2%, 0)", offset: 0.75 }, { transform: "translate3d(3%, -5%, 0)" }], 1.37);
      drift(coreRef.current, [{ transform: "translate3d(5%, -3%, 0) scale(0.94)" }, { transform: "translate3d(-6%, 5%, 0) scale(1.06)", offset: 0.35 }, { transform: "translate3d(3%, 3%, 0) scale(1)", offset: 0.7 }, { transform: "translate3d(5%, -3%, 0) scale(0.94)" }], 0.83);
    };
    start();
    const observer = new ResizeObserver(start);
    observer.observe(host);
    return () => {
      observer.disconnect();
      runs.forEach((r) => r.cancel());
    };
  }, [speed, slant, width, playing]);

  return (
    <div
      ref={hostRef}
      aria-hidden={children ? undefined : true}
      className={className}
      style={{ position: "relative", overflow: "hidden", isolation: "isolate", background: surface, ...style }}
    >
      <div style={{ ...layer, overflow: "hidden", pointerEvents: "none" }}>
        <div ref={trackRef} style={{ position: "absolute", top: "-30%", left: 0, width: `${width}%`, height: "160%", transform: `translateX(-200%) skewX(${slant}deg)` }}>
          <div
            ref={bloomRef}
            style={{ ...layer, opacity: 0.9, filter: `blur(${(26 * soft).toFixed(1)}px)`, backgroundImage: `radial-gradient(ellipse 170% 86% at 42% 50%, ${tone(light[0], 0.14 * k, 0)}, transparent 70%)` }}
          />
          <div
            ref={beamRef}
            style={{ ...layer, filter: `blur(${(10 * soft).toFixed(1)}px)`, backgroundImage: `radial-gradient(ellipse 78% 42% at 43% 50%, ${tone(light[1], 0.33 * k, 1)}, transparent 70%)` }}
          />
          <div
            ref={coreRef}
            style={{ ...layer, filter: `blur(${(2 * soft).toFixed(1)}px)`, backgroundImage: `radial-gradient(ellipse 26% 92% at 50% 50%, ${tone(light[2], 0.55 * k, 2)}, transparent 70%)` }}
          />
        </div>
        {grain > 0 ? <div style={{ ...layer, backgroundImage: GRAIN, backgroundSize: "180px 180px", opacity: grain / 100, mixBlendMode: "overlay" }} /> : null}
      </div>
      {children ? <div style={{ position: "relative" }}>{children}</div> : null}
    </div>
  );
}
