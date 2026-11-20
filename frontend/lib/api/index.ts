import { API_MOCK } from "@/lib/env";
import type { GrowthHackerApi } from "./contract";
import { httpApi } from "./http-api";
import { mockApi } from "./mock/mock-api";

export const api: GrowthHackerApi = API_MOCK ? mockApi : httpApi;
