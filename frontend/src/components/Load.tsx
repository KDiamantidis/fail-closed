import { useState } from "react";
import { ms, postStream } from "../lib/api";
import { Section } from "./ui";

function pct(values: number[], q: number): number | null {
  if (!values.length) return null;
  const v = [...values].sort((a, b) => a - b);
  return v[Math.min(v.length - 1, Math.floor((v.length - 1) * q))];
}

export function Load() {
  const [rows, setRows] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [state, setState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRows([]); setSummary(null); setError(null); setState("running");
    try {
      await postStream("/loadtest/run?normal=50", (type, data) => {
        if (type === "loadtest_request") setRows((r) => [...r, data]);
        if (type === "loadtest_finished") setSummary(data);
        if (type === "loadtest_error") setError(data.error);
      });
      setState("done");
    } catch (e: any) {
      setError(String(e.message ?? e)); setState("error");
    }
  }

  const normal = rows.filter((r) => !r.attack);
  const attack = rows.find((r) => r.attack);
  const allowed = rows.filter((r) => r.verdict === "ALLOW").length;
  const held = rows.filter((r) => r.verdict === "DELAY" || r.verdict === "NEEDS_HUMAN").length;
  const blocked = rows.filter((r) => r.verdict === "BLOCK" || r.verdict === "BUSY").length;
  const affected = normal.filter((r) => r.verdict !== "ALLOW").length;
  const fast = rows.filter((r) => r.path === "fast").map((r) => r.ms);
  const slow = rows.filter((r) => r.path === "slow").map((r) => r.ms);

  const stat = (label: string, value: string, tone = "text-fg", testid?: string) => (
    <div className="panel px-3 py-2" data-testid={testid}>
      <p className="text-[11px] uppercase tracking-wider text-faint">{label}</p>
      <p className={`text-[22px] leading-tight font-bold ${tone}`}>{value}</p>
    </div>
  );

  return (
    <Section id="load" cmd="loadtest --normal 50 --attack 1" title="realistic withdrawals with one hidden attack"
      aside={<button type="button" className="btn btn-signal" onClick={run} disabled={state === "running"} data-testid="load-run">
        {state === "running" ? `running… ${rows.length}/51` : "50 normal + 1 attack"}
      </button>}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="load">
        {stat("allowed", String(allowed), "text-signal")}
        {stat("held", String(held), "text-hold")}
        {stat("blocked", String(blocked), "text-deny")}
        {stat("attack stopped", attack ? (attack.verdict !== "ALLOW" && !attack.signed ? "yes" : "NO") : "–",
          attack ? (attack.verdict !== "ALLOW" ? "text-signal" : "text-deny") : "text-faint", "load-attack")}
        {stat("customers affected", String(affected), affected ? "text-deny" : "text-signal", "load-affected")}
        {stat("requests", `${rows.length}/51`)}
      </div>
      <p className="mt-2 text-[12px] text-dim">
        fast path p50 {ms(pct(fast, 0.5))} · p95 {ms(pct(fast, 0.95))}
        <span className="text-faint"> ({fast.length})</span> · slow path p50 {ms(pct(slow, 0.5))} · p95 {ms(pct(slow, 0.95))}
        <span className="text-faint"> ({slow.length})</span>
        {summary && <span className="text-faint"> · {summary.throughput_rps} req/s · {summary.sandboxes_started} sandboxes · LLM {summary.llm.mode.toUpperCase()} {summary.llm.latency_ms} ms</span>}
      </p>
      {error && <p className="mt-2 text-deny" role="alert">{error}</p>}
    </Section>
  );
}
