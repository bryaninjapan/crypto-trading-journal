import type {
  Summary,
  Analytics,
  Position,
  PositionDetail,
  SymbolRow,
  KlinesResponse,
} from "./types";

// 同源请求；HTTP Basic 由浏览器在首个 401 后自动附带（grill 决策 #8）。
async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} — ${path}`);
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | undefined>): string {
  const p = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return p.length ? `?${p.join("&")}` : "";
}

export interface PositionListParams {
  market?: string;
  symbol?: string;
  direction?: string;
  status?: string;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  analytics: (market = "usdm") => get<Analytics>(`/api/analytics${qs({ market })}`),
  positions: (params: PositionListParams = {}) =>
    get<{ total: number; limit: number; offset: number; positions: Position[] }>(
      `/api/positions${qs(params as Record<string, string | number | undefined>)}`,
    ),
  position: (id: number) => get<PositionDetail>(`/api/positions/${id}`),
  klines: (id: number, interval?: string) =>
    get<KlinesResponse>(`/api/positions/${id}/klines${qs({ interval })}`),
  reports: (market = "usdm") => get<any>(`/api/reports${qs({ market })}`),
  symbols: (market?: string) => get<{ symbols: SymbolRow[] }>(`/api/symbols${qs({ market })}`),
};
