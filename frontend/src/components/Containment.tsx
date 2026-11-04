import { useEffect, useRef, useState } from "react";
import { getJSON, ms, postStream } from "../lib/api";
import { Section } from "./ui";

const PAYLOADS: [string, string][] = [
  ["network", "network"], ["secrets", "secrets"], ["write", "write"], ["fork_bomb", "fork bomb"],
  ["memory_bomb", "memory bomb"], ["cpu_spin", "CPU spin"], ["privilege", "privilege"],
];

interface Ping { ok: boolean; ms: number }

export function Containment({ publicRun, known }: { publicRun: boolean; known: boolean }) {
  const [rows, setRows] = useState<Record<string, any>>({});
  const [state, setState] = useState<"idle" | "running" | "done" | "error" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<any>(null);
  const [pings, setPings] = useState<Ping[]>([]);
  const timer = useRef<number | null>(null);

  // not allowed to run live: show the last saved report
  useEffect(() => {
    if (!known || publicRun) return;
    getJSON("/redteam/last").then((rep) => {
      if (!rep.available) return;
      setRows(Object.fromEntries(rep.results.map((r: any) => [r.name, { ...r, contained: r.contained ? "yes" : "no" }])));
      setSummary({ all_contained: rep.all_contained, red: rep.red, saved_at: rep.ts });
      setState("saved");
    }).catch(() => { /* API down: the panel stays empty */ });
  }, [publicRun, known]);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  async function run() {
    setRows({}); setSummary(null); setError(null); setPings([]); setState("running");
    // our own /health pings while the bombs run: the host must keep answering
    timer.current = window.setInterval(async () => {
      const t0 = performance.now();
      let ok = false;
      try { ok = (await fetch("/health", { cache: "no-store" })).ok; } catch { ok = false; }
      setPings((p) => [...p.slice(-59), { ok, ms: performance.now() - t0 }]);
    }, 500);
    try {
      await postStream("/redteam/run", (type, data) => {
        if (type === "redteam_payload") setRows((r) => ({ ...r, [data.name]: data }));
        if (type === "redteam_finished") setSummary(data);
        if (type === "redteam_error") setError(data.error);
      });
      setState("done");
    } catch (e: any) {
      setError(String(e.message ?? e)); setState("error");
    } finally {
      if (timer.current) window.clearInterval(timer.current);
    }
  }

  const okPings = pings.filter((p) => p.ok);
  const maxPing = okPings.length ? Math.max(...okPings.map((p) => p.ms)) : null;

  return (
    <Section id="containment" cmd="redteam --all" title="hostile code inside the real sandbox, same limits as a check"
      aside={publicRun ? (
        <button type="button" className="btn btn-deny" onClick={run} disabled={state === "running"} data-testid="redteam-run">
          {state === "running" ? "running…" : "run the red team"}
        </button>
      ) : <span className="text-[12px] text-faint">live run is admin-only here · last saved report</span>}>
      <div className="panel overflow-hidden" data-testid="containment" data-state={state}>
        <ul>
          {PAYLOADS.map(([name, label]) => {
            const r = rows[name];
            const status = r ? (r.contained === "yes" ? "CONTAINED" : "NOT CONTAINED") : state === "running" ? "running…" : "—";
            const statusEl = (cls: string) => (
              <span className={`text-[13px] font-bold ${cls} ${status === "CONTAINED" ? "text-signal" : status === "NOT CONTAINED" ? "text-deny" : "text-faint"}`}>
                {status === "CONTAINED" ? "✓ " : status === "NOT CONTAINED" ? "✕ " : ""}{status}
              </span>
            );
            return (
              <li key={name} data-testid={`payload-${name}`} data-status={status}
                className="grid grid-cols-1 gap-1 border-b border-line/60 px-4 py-2.5 last:border-0 sm:grid-cols-[150px_1fr_130px] sm:gap-4">
                {/* phones: the status sits next to the name, so it is seen first */}
                <span className="flex items-baseline justify-between gap-3">
                  <span className="font-bold text-fg">{label}</span>{statusEl("sm:hidden")}
                </span>
                <span className="min-w-0 text-[12px]">
                  {r ? (
                    <>
                      <span className="block text-dim">tried: {r.tried}</span>
                      <span className="block text-faint">stopped by: {r.limit}</span>
                      <span className="block text-dim hex">{r.result}</span>
                      {r.health && <span className="block text-signal">host /health during it: {r.health.answered}/{r.health.calls} answered, max {ms(r.health.max_ms)}</span>}
                    </>
                  ) : <span className="text-faint">waiting</span>}
                </span>
                {statusEl("hidden sm:block sm:text-right")}
              </li>
            );
          })}
        </ul>
        {(state === "running" || pings.length > 0) && (
          <div className="border-t border-line px-4 py-2 text-[12px] text-dim" data-testid="health-pings">
            your browser's /health pings: {okPings.length}/{pings.length} answered{maxPing != null ? `, max ${ms(maxPing)}` : ""}
            <span className="ml-2 inline-flex items-end gap-px align-middle" aria-hidden>
              {pings.slice(-40).map((p, i) => (
                <i key={i} className={`w-1 ${p.ok ? "bg-signal/70" : "bg-deny"}`} style={{ height: `${Math.max(3, Math.min(16, p.ms / 10))}px` }} />
              ))}
            </span>
          </div>
        )}
      </div>
      {summary && (
        <p className={`mt-2 font-bold ${summary.all_contained ? "text-signal" : "text-deny"}`} data-testid="redteam-summary">
          {summary.all_contained ? "All payloads contained." : `NOT contained: ${summary.red.join(", ")}`}
        </p>
      )}
      {error && <p className="mt-2 text-deny" role="alert">{error}</p>}
    </Section>
  );
}
