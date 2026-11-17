import { useQuery } from "@tanstack/react-query";
import type { Desk, Rfq } from "./types";

/** Demo mode reads a recorded desk (public/demo.json). Live mode reads the service API with a desk token. */
const LIVE = import.meta.env.VITE_API_URL as string | undefined;
const TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

async function get<T>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {} });
  } catch {
    throw new ApiError("The desk could not be reached. Check the connection and try again.");
  }
  if (!res.ok) throw new ApiError(`The desk answered ${res.status}.`, res.status);
  return (await res.json()) as T;
}

async function loadDesk(): Promise<Desk> {
  if (!LIVE) return get<Desk>(`${import.meta.env.BASE_URL}demo.json`);
  const [list, reports] = await Promise.all([
    get<Rfq[]>(`${LIVE}/api/rfqs`),
    Promise.all(["dev", "heldout", "bakeoff", "gate"].map((n) => get(`${LIVE}/api/reports/${n}`).catch(() => undefined))),
  ]);
  const rfqs = await Promise.all(list.map((r) => get<Rfq>(`${LIVE}/api/rfqs/${r.message_id}`)));
  const [dev, heldout, bakeoff, gate] = reports as Desk["reports"][keyof Desk["reports"]][];
  return {
    generated_from: "live", today: new Date().toISOString().slice(0, 10), rfqs, lists: [], gateway: {},
    reports: { dev, heldout, bakeoff, gate } as Desk["reports"], mcp: { tools: [], clients: [] },
  };
}

export function useDesk() {
  return useQuery({ queryKey: ["desk"], queryFn: loadDesk, staleTime: 60_000, retry: 1 });
}

export const mode = LIVE ? "live" : "demo";
