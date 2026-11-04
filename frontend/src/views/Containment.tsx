import { useEffect, useRef, useState } from "react";
import { Card, PageHeader, Pill, Spinner } from "../components/ui";
import { getJSON, humanize, ms, postStream } from "../lib/api";
import { useStore } from "../lib/store";

const CHECKS: [string, string][] = [
  ["network", "Reach the internet"],
  ["secrets", "Find keys or secrets"],
  ["write", "Change files"],
  ["fork_bomb", "Fork bomb"],
  ["memory_bomb", "Exhaust memory"],
  ["cpu_spin", "Spin the CPU forever"],
  ["privilege", "Gain privileges"],
];

interface Ping { ok: boolean; ms: number }

// One run per page load is enough; keep the report while the visitor moves between pages.
let saved: { rows: Record<string, any>; summary: any; state: string; pings: Ping[] } | null = null;

export function Containment() {
  const { status } = useStore();
  const [rows, setRows] = useState<Record<string, any>>(saved?.rows ?? {});
  const [state, setState] = useState<string>(saved?.state ?? "idle");
  const [summary, setSummary] = useState<any>(saved?.summary ?? null);
  const [pings, setPings] = useState<Ping[]>(saved?.pings ?? []);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const publicRun = !!status?.redteam_public;

  useEffect(() => { saved = { rows, summary, state, pings }; }, [rows, summary, state, pings]);

  useEffect(() => {
    if (!status || publicRun || state !== "idle") return;
    getJSON("/redteam/last").then((rep) => {
      if (!rep.available) return;
      setRows(Object.fromEntries(rep.results.map((r: any) => [r.name, { ...r, contained: r.contained ? "yes" : "no" }])));
      setSummary({ all_contained: rep.all_contained, red: rep.red, saved_at: rep.ts });
      setState("saved");
    }).catch(() => undefined);
  }, [status, publicRun, state]);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  async function run() {
    setRows({}); setSummary(null); setError(null); setPings([]); setState("running");
    timer.current = window.setInterval(async () => {
      const t0 = performance.now();
      let ok = false;
      try { ok = (await fetch("/health", { cache: "no-store" })).ok; } catch { ok = false; }
      setPings((p) => [...p, { ok, ms: performance.now() - t0 }]);
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
  const health = Object.values(rows).filter((r: any) => r.health);
  const done = Object.keys(rows).length;

  const statusPill = (r: any) => !r
    ? (state === "running" ? <span className="flex items-center gap-1.5 text-[12px] text-muted"><Spinner /> Running</span> : <span className="text-faint">—</span>)
    : r.contained === "yes" ? <Pill tone="ok" dot>Contained</Pill> : <Pill tone="bad" dot>Not contained</Pill>;

  return (
    <>
      <PageHeader title="Sandbox isolation report"
        lead="Every unusual request runs in a sandbox. This test runs hostile code inside that same sandbox, with the same limits, and measures whether anything gets out or slows the host down."
        action={publicRun ? (
          <button type="button" className="btn btn-primary" onClick={run} disabled={state === "running"} data-testid="redteam-run">
            {state === "running" ? <><Spinner /> Running {done}/7</> : "Run isolation test"}
          </button>
        ) : <Pill>Live test: operators only · showing the last report</Pill>} />

      {summary && (
        <div data-testid="redteam-summary"
          className={`mb-6 rounded-xl border px-5 py-4 ${summary.all_contained ? "border-ok-line bg-ok-bg" : "border-bad-line bg-bad-bg"}`}>
          <p className={`text-[16px] font-semibold ${summary.all_contained ? "text-ok" : "text-bad"}`}>
            {summary.all_contained ? "All 7 checks contained." : `Not contained: ${summary.red.join(", ")}`}
          </p>
          <p className="mt-0.5 text-[13px] text-body">
            {summary.all_contained ? "Nothing reached the network, the host or a secret, and the Guard kept answering throughout." : "This sandbox build must not be used."}
            {summary.saved_at ? ` Report from ${new Date(summary.saved_at * 1000).toLocaleString()}.` : ""}
          </p>
        </div>
      )}

      <Card pad={false} testid="containment">
        <div data-state={state}>
          <table className="hidden w-full text-left text-[13px] md:table">
            <thead className="border-b border-border bg-subtle text-[12px] text-muted">
              <tr>
                <th className="w-[230px] px-5 py-2.5 font-medium">What was tried</th>
                <th className="px-3 py-2.5 font-medium">Control that stops it</th>
                <th className="px-3 py-2.5 font-medium">Measured result</th>
                <th className="w-[140px] px-5 py-2.5 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {CHECKS.map(([name, label]) => {
                const r = rows[name];
                return (
                  <tr key={name} data-testid={`payload-${name}`} data-status={r ? (r.contained === "yes" ? "CONTAINED" : "NOT CONTAINED") : "-"} className="align-top">
                    <td className="px-5 py-3">
                      <p className="font-medium text-ink">{label}</p>
                      {r && <p className="mt-0.5 text-[12px] text-muted">{r.tried}</p>}
                    </td>
                    <td className="px-3 py-3 text-body">{r ? <span className="mono wrap-any">{r.limit}</span> : <span className="text-faint">—</span>}</td>
                    <td className="px-3 py-3 text-body">
                      {r ? <span className="wrap-any">{humanize(r.result)}</span> : <span className="text-faint">—</span>}
                    </td>
                    <td className="px-5 py-3 text-right">{statusPill(r)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <ul className="divide-y divide-border md:hidden">
            {CHECKS.map(([name, label]) => {
              const r = rows[name];
              return (
                <li key={name} className="px-4 py-3" data-testid={`payload-m-${name}`} data-status={r ? (r.contained === "yes" ? "CONTAINED" : "NOT CONTAINED") : "-"}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-ink">{label}</span>{statusPill(r)}
                  </div>
                  {r && (
                    <dl className="mt-1.5 space-y-1 text-[13px]">
                      <div><dt className="inline text-muted">Control: </dt><dd className="inline text-body">{r.limit}</dd></div>
                      <div><dt className="inline text-muted">Result: </dt><dd className="inline text-body wrap-any">{humanize(r.result)}</dd></div>
                    </dl>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </Card>

      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <Card title="Host health during the test" testid="host-health">
          {health.length === 0 && pings.length === 0 ? (
            <p className="text-[13px] text-muted">Measured while the fork bomb and the memory bomb run.</p>
          ) : (
            <ul className="space-y-2 text-[13px]">
              {health.map((r: any) => (
                <li key={r.name} className="flex items-center justify-between gap-3">
                  <span className="text-body">Guard answered during the {r.name === "memory_bomb" ? "memory test" : r.name === "fork_bomb" ? "fork bomb" : r.name.replace(/_/g, " ")}</span>
                  <span className="font-medium tabular-nums text-ink">{r.health.answered}/{r.health.calls} · max {ms(r.health.max_ms)}</span>
                </li>
              ))}
              {pings.length > 0 && (
                <li className="flex items-center justify-between gap-3" data-testid="health-pings">
                  <span className="text-body">Your browser's health checks</span>
                  <span className="font-medium tabular-nums text-ink">{okPings.length}/{pings.length}{maxPing != null ? ` · max ${ms(maxPing)}` : ""}</span>
                </li>
              )}
            </ul>
          )}
        </Card>
        <Card title="Sandbox limits">
          <dl className="grid grid-cols-2 gap-3 text-[13px]">
            <div><dt className="label">Network</dt><dd className="text-ink">None</dd></div>
            <div><dt className="label">File system</dt><dd className="text-ink">Read-only</dd></div>
            <div><dt className="label">Memory</dt><dd className="text-ink">512 MB, no swap</dd></div>
            <div><dt className="label">Processes</dt><dd className="text-ink">128 at most</dd></div>
            <div><dt className="label">Privileges</dt><dd className="text-ink">All dropped</dd></div>
            <div><dt className="label">Secrets</dt><dd className="text-ink">None inside</dd></div>
          </dl>
        </Card>
      </div>
      {error && <p className="mt-4 text-bad" role="alert">{error}</p>}
    </>
  );
}
