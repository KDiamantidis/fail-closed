import { LatencyChart } from "../components/LatencyChart";
import { Card, PageHeader, Spinner } from "../components/ui";
import { ms } from "../lib/api";
import { useStore } from "../lib/store";

function pct(values: number[], q: number): number | null {
  if (!values.length) return null;
  const v = [...values].sort((a, b) => a - b);
  return v[Math.min(v.length - 1, Math.floor((v.length - 1) * q))];
}

function Counter({ label, value, tone = "text-ink", testid }: { label: string; value: string; tone?: string; testid?: string }) {
  return (
    <div className="rounded-lg border border-border px-4 py-3" data-testid={testid}>
      <p className="label">{label}</p>
      <p className={`mt-0.5 text-[22px] font-semibold tabular-nums leading-tight ${tone}`}>{value}</p>
    </div>
  );
}

export function Simulation() {
  const { sim, runSim } = useStore();
  const rows = sim.rows;
  const normal = rows.filter((r) => !r.attack);
  const attack = rows.find((r) => r.attack);
  const approved = rows.filter((r) => r.verdict === "ALLOW").length;
  const held = rows.filter((r) => r.verdict === "DELAY" || r.verdict === "NEEDS_HUMAN").length;
  const blocked = rows.filter((r) => r.verdict === "BLOCK" || r.verdict === "BUSY").length;
  const affected = normal.filter((r) => r.verdict !== "ALLOW").length;
  const fast = rows.filter((r) => r.path !== "slow").map((r) => r.ms);
  const slow = rows.filter((r) => r.path === "slow").map((r) => r.ms);
  const stopped = attack ? attack.verdict !== "ALLOW" && !attack.signed : null;
  const running = sim.status === "running";

  return (
    <>
      <PageHeader title="50 withdrawals + 1 attack"
        lead="A normal stream of customer withdrawals from three hot wallets, with one delegation attack hidden inside. It shows two things at once: the attack is stopped, and customers are not slowed down."
        action={
          <button type="button" className="btn btn-primary" onClick={runSim} disabled={running} data-testid="load-run">
            {running ? <><Spinner /> Running {rows.length}/51</> : sim.status === "idle" ? "Run simulation" : "Run again"}
          </button>
        } />

      <Card testid="load">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Counter label="Requests" value={`${rows.length}/51`} />
          <Counter label="Approved" value={String(approved)} tone={approved ? "text-ok" : "text-ink"} />
          <Counter label="Held or review" value={String(held)} />
          <Counter label="Blocked" value={String(blocked)} tone={blocked ? "text-bad" : "text-ink"} />
          <Counter label="Attack stopped" testid="load-attack"
            value={stopped === null ? "–" : stopped ? "Yes" : "No"} tone={stopped === false ? "text-bad" : stopped ? "text-ok" : "text-ink"} />
          <Counter label="Customers affected" testid="load-affected" value={rows.length ? String(affected) : "–"}
            tone={affected ? "text-bad" : rows.length ? "text-ok" : "text-ink"} />
        </div>
        <div className="mt-5">
          {rows.length === 0 ? (
            <div className="flex h-[220px] items-center justify-center rounded-lg border border-dashed border-border-strong bg-subtle text-[13px] text-muted">
              Runs on its own isolated state. Takes about 10 seconds.
            </div>
          ) : <LatencyChart rows={rows} />}
        </div>
        {rows.length > 0 && (
          <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-4 text-[13px] sm:grid-cols-4" data-testid="load-latency">
            <div><dt className="label">Instant check, median</dt><dd className="tabular-nums text-ink">{ms(pct(fast, 0.5))} <span className="text-faint">({fast.length})</span></dd></div>
            <div><dt className="label">Instant check, 95th pct.</dt><dd className="tabular-nums text-ink">{ms(pct(fast, 0.95))}</dd></div>
            <div><dt className="label">Sandbox check, median</dt><dd className="tabular-nums text-ink">{ms(pct(slow, 0.5))} <span className="text-faint">({slow.length})</span></dd></div>
            <div><dt className="label">Throughput</dt><dd className="tabular-nums text-ink">{sim.summary ? `${sim.summary.throughput_rps} requests/s` : "…"}</dd></div>
          </dl>
        )}
        {sim.summary?.llm && (
          <p className="mt-3 text-[12px] text-muted">
            Instant checks include the AI's quick look at each request ({sim.summary.llm.mode === "mock" ? "mock, " : ""}{sim.summary.llm.latency_ms} ms here).
            The AI can only ask for a sandbox check; it never approves or blocks.
          </p>
        )}
        {sim.error && <p className="mt-3 text-bad" role="alert">{sim.error}</p>}
      </Card>
    </>
  );
}
