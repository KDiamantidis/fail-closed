import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getJSON, newRunId, postJSON, postStream, subscribeRun, type GuardEvent, type Verdict } from "./api";

export interface ScenarioInfo {
  name: string;
  title: string;
  description: string;
  labels: Record<string, string>;
  requests: { label: string; plain?: string }[];
}

export interface Run {
  runId: string;
  scenario: ScenarioInfo;
  startedAt: number;
  status: "running" | "done" | "error";
  error?: string;
  events: GuardEvent[];
  result?: any;
  decisions?: Record<string, any>;
}

/** One checked request, flattened from a replay result for tables and KPIs. */
export interface Row {
  key: string;
  run: Run;
  index: number;
  item: any;
  check: any;
  received: any;     // the 'received' event data (wallet, destination, value, chain)
  ts: number;
}

export interface SimState {
  status: "idle" | "running" | "done" | "error";
  rows: any[];
  summary?: any;
  error?: string;
}

export interface Status {
  sandbox_mode: string;
  llm: "REAL" | "MOCK" | "OFF";
  model: string;
  breaker_level: number;
  redteam_public: boolean;
  delay_minutes: number;
}

interface Store {
  status: Status | null;
  scenarios: ScenarioInfo[];
  runs: Run[];
  rows: Row[];
  sim: SimState;
  running: boolean;
  runReplay: (name: string) => Promise<Run | null>;
  runSim: () => Promise<void>;
  decide: (runId: string, holdId: string, decision: "approve" | "reject") => Promise<void>;
  route: string;
  go: (to: string) => void;
}

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}

function useHashRoute(): [string, (to: string) => void] {
  const read = () => window.location.hash.replace(/^#/, "") || "/overview";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => { setRoute(read()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((to: string) => { window.location.hash = to; }, []);
  return [route, go];
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [sim, setSim] = useState<SimState>({ status: "idle", rows: [] });
  const [route, go] = useHashRoute();
  const closers = useRef<(() => void)[]>([]);

  useEffect(() => {
    const load = () => getJSON<Status>("/status").then(setStatus).catch(() => setStatus(null));
    load();
    getJSON<ScenarioInfo[]>("/scenarios").then(setScenarios).catch(() => setScenarios([]));
    const t = window.setInterval(load, 15000);
    return () => { window.clearInterval(t); closers.current.forEach((c) => c()); };
  }, []);

  const patch = useCallback((runId: string, f: (r: Run) => Run) =>
    setRuns((rs) => rs.map((r) => (r.runId === runId ? f(r) : r))), []);

  const running = runs.some((r) => r.status === "running");

  const runReplay = useCallback(async (name: string) => {
    const sc = scenarios.find((s) => s.name === name);
    if (!sc) return null;
    const runId = newRunId();
    const run: Run = { runId, scenario: sc, startedAt: Date.now(), status: "running", events: [] };
    setRuns((rs) => [run, ...rs].slice(0, 12));
    const close = await subscribeRun(runId, (ev) => patch(runId, (r) => ({ ...r, events: [...r.events, ev] })));
    closers.current.push(close);
    try {
      const result = await postJSON(`/scenarios/${encodeURIComponent(name)}/run?run_id=${runId}`);
      patch(runId, (r) => ({ ...r, status: "done", result }));
      window.setTimeout(close, 20000); // AI explanations keep arriving after the verdicts
      return { ...run, status: "done", result } as Run;
    } catch (e: any) {
      close();
      patch(runId, (r) => ({ ...r, status: "error", error: String(e.message ?? e) }));
      return null;
    }
  }, [scenarios, patch]);

  const runSim = useCallback(async () => {
    setSim({ status: "running", rows: [] });
    try {
      await postStream("/loadtest/run?normal=50", (type, data) => {
        if (type === "loadtest_request") setSim((s) => ({ ...s, rows: [...s.rows, data] }));
        if (type === "loadtest_finished") setSim((s) => ({ ...s, summary: data }));
        if (type === "loadtest_error") setSim((s) => ({ ...s, error: data.error }));
      });
      setSim((s) => ({ ...s, status: s.error ? "error" : "done" }));
    } catch (e: any) {
      setSim((s) => ({ ...s, status: "error", error: String(e.message ?? e) }));
    }
  }, []);

  const decide = useCallback(async (runId: string, holdId: string, decision: "approve" | "reject") => {
    let r: any;
    try {
      r = await postJSON(`/demo/${runId}/pending/${encodeURIComponent(holdId)}/${decision}`);
    } catch (e: any) {
      r = { status: "error", signed: false, reason: String(e.message ?? e) };
    }
    let pending: any[] | null = null;
    try { pending = await getJSON<any[]>(`/demo/${runId}/pending`); } catch { pending = null; }
    patch(runId, (run) => ({
      ...run,
      decisions: { ...(run.decisions ?? {}), [holdId]: { ...r, decision } },
      result: pending ? { ...run.result, pending } : run.result,
    }));
  }, [patch]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    for (const run of runs) {
      const items: any[] = run.result?.results ?? [];
      items.forEach((item, index) => {
        if (!item.check) return;
        const received = run.events.find((e) => e.check_id === item.check.check_id && e.type === "received");
        out.push({ key: `${run.runId}.${item.check.check_id}`, run, index, item, check: item.check,
          received: received?.data ?? {}, ts: received ? received.ts * 1000 : run.startedAt + index });
      });
    }
    return out.sort((a, b) => b.ts - a.ts);
  }, [runs]);

  const value = useMemo<Store>(() => ({ status, scenarios, runs, rows, sim, running, runReplay, runSim, decide, route, go }),
    [status, scenarios, runs, rows, sim, running, runReplay, runSim, decide, route, go]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// ---- derived numbers for the overview ------------------------------------------------------

const ATTACK_RULES = new Set(["R1", "R2", "R3", "R7", "R8", "F1", "F4"]);

export function isAttackStop(check: any): boolean {
  return check.verdict === "BLOCK" &&
    (check.findings ?? []).some((f: any) => f.action === "BLOCK" && ATTACK_RULES.has(f.rule_id));
}

/** ETH that a blocked request would have moved: its own value, or what the probe saw leave. */
export function weiAtRisk(row: Row): bigint {
  let v = 0n;
  try { v = BigInt(row.received?.value ?? "0"); } catch { v = 0n; }
  for (const p of row.check.simulation?.probe ?? []) {
    let moved = 0n;
    for (const amt of Object.values(p.native_out ?? {})) {
      try { moved += BigInt(amt as string); } catch { /* ignore */ }
    }
    if (moved > v) v = moved;
  }
  return v;
}

export function kpis(rows: Row[], sim: SimState) {
  const count = (v: Verdict) => rows.filter((r) => r.check.verdict === v).length +
    sim.rows.filter((r) => r.verdict === v).length;
  const stops = rows.filter((r) => isAttackStop(r.check));
  const simAttack = sim.rows.find((r) => r.attack);
  const protectedWei = stops.reduce((acc, r) => acc + weiAtRisk(r), 0n);
  return {
    checked: rows.length + sim.rows.length,
    approved: count("ALLOW"),
    held: count("DELAY"),
    review: count("NEEDS_HUMAN"),
    blocked: count("BLOCK") + sim.rows.filter((r) => r.verdict === "BUSY").length,
    attacks: new Set(stops.map((r) => r.run.runId)).size + (simAttack && simAttack.verdict !== "ALLOW" && !simAttack.signed ? 1 : 0),
    protectedWei,
    affected: sim.rows.filter((r) => !r.attack && r.verdict !== "ALLOW").length,
    simRan: sim.rows.length > 0,
  };
}

export function breakerLevel(state: any): number {
  if (!state) return 0;
  if (state.global_stop) return 4;
  if (state.safe_mode_chains?.length) return 3;
  if (state.paused_wallets?.length) return 2;
  if (state.frozen_destinations?.length) return 1;
  return 0;
}

export function latestBreaker(runs: Run[]): { state: any; source: string } | null {
  const r = runs.find((x) => x.result?.breaker);
  return r ? { state: r.result.breaker, source: r.scenario.name } : null;
}

/** Explanation events for a check (made after the verdict, async). */
export function explanationFor(run: Run, checkId: string): any {
  return run.events.find((e) => e.type === "explanation" && e.check_id === checkId)?.data;
}
