import { useEffect, useMemo, useRef, useState } from "react";
import { getJSON, newRunId, postJSON, subscribeRun, type GuardEvent } from "../lib/api";
import { RequestCard } from "./RequestCard";
import { Section, VerdictPill } from "./ui";

export interface ScenarioInfo {
  name: string;
  title: string;
  description: string;
  labels: Record<string, string>;
  requests: { label: string }[];
}

export interface RunState {
  runId: string;
  scenario: ScenarioInfo;
  status: "running" | "done" | "error";
  error?: string;
  events: GuardEvent[];
  result?: any;
}

const STEPS: [string, string][] = [
  ["received", "received"], ["decoded", "decoded"], ["request_rules", "rules"], ["path", "path"],
  ["sandbox_started", "sandbox"], ["sandbox_done", "diff"], ["findings", "findings"], ["verdict", "verdict"],
  ["approval", "approval"], ["signer", "signer"],
];

function Timeline({ events, total }: { events: GuardEvent[]; total: number }) {
  // the most recent check of the run
  const checks = useMemo(() => {
    const order: string[] = [];
    for (const e of events) if (e.check_id !== "-" && e.type === "received" && !order.includes(e.check_id)) order.push(e.check_id);
    return order;
  }, [events]);
  const current = checks[checks.length - 1];
  const mine = events.filter((e) => e.check_id === current);
  const seen = new Set(mine.map((e) => e.type));
  const t0 = mine[0]?.ts ?? 0;
  const path = mine.find((e) => e.type === "path")?.data?.path;
  const verdict = mine.find((e) => e.type === "verdict")?.data?.verdict;
  return (
    <div className="panel px-4 py-3" data-testid="timeline" aria-live="polite">
      <p className="mb-2 text-[12px] text-faint">
        live · request {checks.length || 0}/{total}{path ? ` · ${path} path` : ""}
      </p>
      <ol className="flex flex-wrap gap-1.5">
        {STEPS.map(([type, label]) => {
          const ev = mine.find((e) => e.type === type);
          const skipped = !ev && (type === "sandbox_started" || type === "sandbox_done") && (path === "fast" || path === "early");
          const on = seen.has(type);
          return (
            <li key={type}
              className={`rounded border px-2 py-0.5 text-[12px] transition-colors duration-200 ${on ? "border-signal/60 bg-signal/10 text-signal" : skipped ? "border-line text-faint line-through" : "border-line text-faint"}`}>
              {label}{on && ev ? <span className="text-signal/70"> {Math.round((ev.ts - t0) * 1000)}ms</span> : null}
            </li>
          );
        })}
        {verdict && <li><VerdictPill v={verdict} /></li>}
      </ol>
    </div>
  );
}

export function Scenarios({ run, setRun }: { run: RunState | null; setRun: (f: (r: RunState | null) => RunState | null) => void }) {
  const [list, setList] = useState<ScenarioInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    getJSON<ScenarioInfo[]>("/scenarios").then(setList).catch((e) => setLoadError(String(e.message ?? e)));
    return () => closeRef.current?.();
  }, []);

  async function start(sc: ScenarioInfo) {
    closeRef.current?.();
    const runId = newRunId();
    setRun(() => ({ runId, scenario: sc, status: "running", events: [] }));
    const close = await subscribeRun(runId, (ev) =>
      setRun((r) => (r && r.runId === runId ? { ...r, events: [...r.events, ev] } : r)));
    closeRef.current = close;
    try {
      const result = await postJSON(`/scenarios/${encodeURIComponent(sc.name)}/run?run_id=${runId}`);
      setRun((r) => (r && r.runId === runId ? { ...r, status: "done", result } : r));
      // explanations keep arriving after the run; stop listening a little later
      setTimeout(() => { if (closeRef.current === close) close(); }, 20000);
    } catch (e: any) {
      close();
      setRun((r) => (r && r.runId === runId ? { ...r, status: "error", error: String(e.message ?? e) } : r));
    }
  }

  const explanations = useMemo(() => {
    const m: Record<string, any> = {};
    for (const e of run?.events ?? []) if (e.type === "explanation") m[e.check_id] = e.data;
    return m;
  }, [run?.events]);

  const heldOutcome = useMemo(() => {
    const m: Record<string, any> = {};
    const w = run?.result?.delay_worker;
    for (const d of w?.results ?? []) m[d.id] = { ...d, skipped: w.skipped_minutes };
    return m;
  }, [run?.result]);

  const running = run?.status === "running";
  const items: any[] = run?.result?.results ?? [];
  const counts = items.reduce((acc: Record<string, number>, it: any) => {
    if (it.check) acc[it.check.verdict] = (acc[it.check.verdict] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <Section id="scenarios" cmd="run scenario" title="each run gets its own fresh, isolated state">
      {loadError && <p className="text-deny">could not load scenarios: {loadError}</p>}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {list.map((sc) => {
          const m = /^\(([a-z])\)\s*(.*)$/.exec(sc.title);
          const active = run?.scenario.name === sc.name;
          return (
            <button key={sc.name} type="button" disabled={running} onClick={() => start(sc)}
              data-testid={`scenario-${sc.name}`}
              className={`btn btn-ghost h-full items-start justify-start text-left font-normal ${active ? "border-signal/60 text-fg" : ""}`}>
              <span className="font-bold text-signal">{m?.[1] ?? "·"}</span>
              <span className="min-w-0">{m?.[2] ?? sc.title}</span>
            </button>
          );
        })}
      </div>

      {run && (
        <div className="mt-4 space-y-3" data-testid="run" data-status={run.status}>
          <p className="text-[12px] text-faint">
            run <span className="text-dim">{run.runId}</span> · {run.scenario.description}
          </p>
          <Timeline events={run.events} total={run.scenario.requests.length} />
          {run.status === "error" && <p className="text-deny" role="alert">run failed: {run.error}</p>}
          {run.status === "done" && (
            <div className="flex flex-wrap items-center gap-2 text-[12px]" data-testid="run-summary">
              <span className="text-faint">result:</span>
              {(["ALLOW", "DELAY", "NEEDS_HUMAN", "BLOCK"] as const).filter((v) => counts[v]).map((v) => (
                <span key={v} className="flex items-center gap-1"><VerdictPill v={v} /> ×{counts[v]}</span>
              ))}
              {(run.result?.delay_worker?.results ?? []).length > 0 && (
                <span className="chip border-hold/50 text-hold">demo: {run.result.delay_worker.skipped_minutes} min skipped for DELAY</span>
              )}
            </div>
          )}
          <div className="space-y-2">
            {items.map((it, i) => (
              <RequestCard key={i} index={i + 1} item={it} labels={run.scenario.labels ?? {}}
                explanation={it.check ? explanations[it.check.check_id] : undefined}
                held={it.check ? heldOutcome[it.check.check_id] : undefined} />
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}
