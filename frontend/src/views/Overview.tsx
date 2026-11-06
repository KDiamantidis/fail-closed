import { useEffect, useState } from "react";
import { ActivityFeed } from "../components/ActivityFeed";
import { BlastRadius } from "../components/BlastRadius";
import { BreakerLadder } from "../components/BreakerLadder";
import { ResetDemoButton, RiskKpi } from "../components/Risk";
import { Card, PageHeader, Pill, Spinner, VerdictPill } from "../components/ui";
import { eth } from "../lib/api";
import { REPLAYS } from "../lib/copy";
import { kpis, useStore, type Run } from "../lib/store";

function Kpi({ label, value, note, tone = "text-ink", testid }:
  { label: string; value: string; note?: string; tone?: string; testid?: string }) {
  return (
    <div className="card px-4 py-3" data-testid={testid}>
      <p className="label">{label}</p>
      <p className={`mt-1 text-[24px] font-semibold tabular-nums leading-tight ${tone}`}>{value}</p>
      {note && <p className="mt-0.5 text-[12px] text-faint">{note}</p>}
    </div>
  );
}

function outcome(run: Run | undefined) {
  if (!run) return null;
  if (run.status === "running") return <span className="flex items-center gap-1.5 text-[12px] text-muted"><Spinner /> Running…</span>;
  if (run.status === "error") return <span className="text-[12px] text-bad" role="alert">
    {/^Too many/.test(run.error ?? "") ? run.error : `Failed: ${run.error}`}</span>;
  const checks = (run.result?.results ?? []).filter((x: any) => x.check);
  const counts: Record<string, number> = {};
  for (const x of checks) counts[x.check.verdict] = (counts[x.check.verdict] ?? 0) + 1;
  return (
    <span className="flex flex-wrap items-center gap-1.5" data-testid={`replay-outcome-${run.scenario.name}`}>
      {(["ALLOW", "DELAY", "NEEDS_HUMAN", "BLOCK"] as const).filter((v) => counts[v]).map((v) => (
        <span key={v} className="flex items-center gap-1"><VerdictPill v={v} /><span className="text-[12px] text-muted">×{counts[v]}</span></span>
      ))}
      <a href={`#/requests?run=${run.runId}`} className="ml-1 text-[13px]">View requests →</a>
    </span>
  );
}

const TOUR_KEY = "fc.tour.hidden";

function readHidden(): boolean {
  try { return window.localStorage.getItem(TOUR_KEY) === "1"; } catch { return false; }
}

function saveHidden(v: boolean) {
  try { window.localStorage.setItem(TOUR_KEY, v ? "1" : "0"); } catch { /* private window: per page view only */ }
}

/** First visit: three steps. Hidden after the first replay; a small link brings it back. */
function Tour({ hasRuns }: { hasRuns: boolean }) {
  const [hidden, setHidden] = useState(readHidden);
  useEffect(() => { if (hasRuns && !hidden) { setHidden(true); saveHidden(true); } }, [hasRuns]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (hidden) {
    return (
      <p className="mb-4 text-[13px]">
        <button type="button" className="cursor-pointer font-medium text-brand hover:text-brand-hover" data-testid="tour-show"
          onClick={() => { setHidden(false); saveHidden(false); }}>Try it in 60 seconds</button>
      </p>
    );
  }
  return (
    <section className="card mb-6 border-[#99e6d4] px-5 py-4" data-testid="tour" aria-label="Try it in 60 seconds">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-[15px] font-semibold text-ink">Try it in 60 seconds</h2>
        <button type="button" className="cursor-pointer text-[13px] font-medium text-muted hover:text-ink" data-testid="tour-hide"
          onClick={() => { setHidden(true); saveHidden(true); }}>Hide</button>
      </div>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-[13.5px] text-body">
        <li>Run <span className="font-medium text-ink">Hot wallet delegation drain</span>: the Guard blocks it and nothing is signed.</li>
        <li>Run <span className="font-medium text-ink">Coordinated attack</span> and watch the risk level rise to the global stop.</li>
        <li>Press <span className="font-medium text-ink">Reset demo</span> to go back to level 0.</li>
      </ol>
    </section>
  );
}

export function Overview() {
  const { rows, sim, runs, running, runReplay, scenarios, risk } = useStore();
  const k = kpis(rows, sim);
  const latestWithBlast = runs.find((r) => r.result?.blast_radius?.length);
  const lastRun = (name: string) => runs.find((r) => r.scenario.name === name);
  const ready = scenarios.length > 0;

  const replayRow = (r: (typeof REPLAYS)[number]) => (
    <li key={r.name} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="font-medium text-ink">{r.title}</p>
        <p className="text-[13px] text-muted">{r.blurb}</p>
        <div className="mt-1.5">{outcome(lastRun(r.name))}</div>
      </div>
      <button type="button" className="btn btn-secondary shrink-0 self-start sm:self-auto"
        disabled={!ready || running} onClick={() => runReplay(r.name)} data-testid={`replay-${r.name}`}>
        <svg viewBox="0 0 16 16" className="size-3.5 text-brand" fill="currentColor" aria-hidden><path d="M5 3.5v9l7-4.5z" /></svg>
        Run replay
      </button>
    </li>
  );

  return (
    <>
      <PageHeader title="Overview"
        lead="Every signing request is checked before a signature exists. Unusual requests run in an isolated sandbox first; fixed rules decide, and nothing is signed without the Guard's approval." />

      <Tour hasRuns={runs.length > 0} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5" data-testid="kpis">
        <RiskKpi />
        <Kpi label="Requests checked" value={String(k.checked)} testid="kpi-checked" />
        <Kpi label="Approved" value={String(k.approved)} tone={k.approved ? "text-ok" : "text-ink"} />
        <Kpi label="Held" value={String(k.held)} tone={k.held ? "text-held" : "text-ink"} />
        <Kpi label="Needs review" value={String(k.review)} tone={k.review ? "text-review" : "text-ink"} />
        <Kpi label="Blocked" value={String(k.blocked)} tone={k.blocked ? "text-bad" : "text-ink"} />
        <Kpi label="Attacks stopped" value={String(k.attacks)} note="replays and simulation" testid="kpi-attacks" />
        <Kpi label="Funds protected" value={eth(k.protectedWei)} note="ETH in blocked requests" testid="kpi-protected" />
        <Kpi label="Customers affected" value={k.simRan ? String(k.affected) : "–"} tone={k.affected ? "text-bad" : "text-ink"}
          note={k.simRan ? "in the simulation" : "run the simulation"} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <Card title="Incident replays" testid="replays"
            action={<span className="flex items-center gap-3">
              <span className="hidden text-[12px] text-muted md:block">Each replay runs on its own isolated state</span>
              <ResetDemoButton className="btn btn-secondary px-2.5 py-1.5" />
            </span>}>
            <div className="-mt-1 mb-2 space-y-1 rounded-lg bg-subtle px-3 py-2.5 text-[13px]">
              <p><span className="font-medium text-ink">Delegation drain.</span> One signed transaction hands a hot wallet to an attacker's contract (EIP-7702), which can then empty it.</p>
              <p><span className="font-medium text-ink">Multi-chain drain.</span> With a stolen key, plain transfers send everything to one address on many chains, token by token.</p>
            </div>
            <ul className="divide-y divide-border">{REPLAYS.filter((r) => r.featured).map(replayRow)}</ul>
            <p className="mt-4 mb-1 label">Everyday cases</p>
            <ul className="divide-y divide-border">{REPLAYS.filter((r) => !r.featured).map(replayRow)}</ul>
          </Card>
          {latestWithBlast && <BlastRadius data={latestWithBlast.result.blast_radius} labels={latestWithBlast.scenario.labels} />}
        </div>
        <div className="space-y-6">
          <Card title="Circuit breaker" action={<a href="#/breaker" className="text-[13px]">Details →</a>}>
            <p className="mb-3 text-[13px] text-muted">
              {risk.runId ? "State of your latest replay." : "Live state. Run a replay to see it react."}
            </p>
            <BreakerLadder state={risk.state} labels={runs[0]?.scenario.labels ?? {}} compact />
          </Card>
          <Card title="Live activity" action={running ? <Pill tone="brand" dot>Live</Pill> : undefined}>
            <ActivityFeed runs={runs} />
          </Card>
        </div>
      </div>
    </>
  );
}
