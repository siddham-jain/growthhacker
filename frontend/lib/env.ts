export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

export const API_MOCK = process.env.NEXT_PUBLIC_API_MOCK === "1";

export const DEMO_WORKSPACE = "demo";
