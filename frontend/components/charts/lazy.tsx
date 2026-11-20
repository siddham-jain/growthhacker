"use client";

import dynamic from "next/dynamic";
import { LoadingBlock } from "@/components/states/states";

// charts are below the fold on every screen, so they load after the page is interactive
const placeholder = () => <LoadingBlock rows={2} />;

export const CadenceChart = dynamic(() => import("./cadence-chart").then((module) => module.CadenceChart), { ssr: false, loading: placeholder });
export const ViewsAtAgeChart = dynamic(() => import("./views-at-age-chart").then((module) => module.ViewsAtAgeChart), { ssr: false, loading: placeholder });
export const PerformanceChart = dynamic(() => import("./performance-chart").then((module) => module.PerformanceChart), { ssr: false, loading: placeholder });
export const GapMatrix = dynamic(() => import("./gap-matrix").then((module) => module.GapMatrix), { ssr: false, loading: placeholder });
