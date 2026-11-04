import { BlastRadius } from "../components/BlastRadius";
import { Addr, Card, Empty, PageHeader, VerdictPill } from "../components/ui";
import { eth, ms } from "../lib/api";
import { PATH_WORD, REPLAY_TITLE } from "../lib/copy";
import { useStore, type Row } from "../lib/store";
import { RequestDetail } from "./RequestDetail";

export function amountText(row: Row): string {
  const d = row.check.decoded;
  const labels = row.run.scenario.labels ?? {};
  if (d?.kind === "delegation") return `Delegation · ${row.received?.authorizations ?? "?"} authorizations`;
  if (!d) return eth(row.received?.value ?? "0");
  if (d.asset === "native") return eth(d.amount);
  const units = eth(d.amount).replace(" ETH", "");
  return `${units} ${labels[d.asset] ?? "tokens"}`;
}

function time(ts: number) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function Requests() {
  const { rows, runs, route, go } = useStore();
  const [path, query] = route.split("?");
  const detailKey = path.startsWith("/requests/") ? decodeURIComponent(path.slice("/requests/".length)) : null;
  const runFilter = new URLSearchParams(query ?? "").get("run");

  if (detailKey) {
    const row = rows.find((r) => r.key === detailKey);
    return row ? <RequestDetail row={row} /> : (
      <>
        <PageHeader title="Signing request" />
        <Empty title="This request is no longer in this session">
          <a href="#/requests">Back to all requests</a>
        </Empty>
      </>
    );
  }

  const shown = runFilter ? rows.filter((r) => r.run.runId === runFilter) : rows;
  const run = runFilter ? runs.find((r) => r.runId === runFilter) : undefined;
  const ordered = run ? [...shown].sort((a, b) => a.index - b.index) : shown;

  return (
    <>
      <PageHeader title="Signing requests"
        lead="Every request the withdrawal service sent for signing, with the Guard's decision. Select a row for the evidence." />

      {runs.length > 0 && (
        <div className="-mx-4 mb-4 flex items-center gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="Filter by replay">
          <button type="button" className={`btn ${!runFilter ? "btn-primary" : "btn-secondary"}`} onClick={() => go("/requests")}>
            All replays
          </button>
          {runs.filter((r) => r.result).map((r) => (
            <button key={r.runId} type="button" onClick={() => go(`/requests?run=${r.runId}`)}
              className={`btn ${runFilter === r.runId ? "btn-primary" : "btn-secondary"}`}>
              {REPLAY_TITLE[r.scenario.name] ?? r.scenario.name}
            </button>
          ))}
        </div>
      )}

      {run?.result?.blast_radius?.length ? (
        <div className="mb-6"><BlastRadius data={run.result.blast_radius} labels={run.scenario.labels} /></div>
      ) : null}

      {ordered.length === 0 ? (
        <Empty title="No signing requests yet">
          Run an incident replay on the <a href="#/overview">Overview</a> page.
        </Empty>
      ) : (
        <Card pad={false} testid="requests">
          {/* desktop: table */}
          <table className="hidden w-full text-left text-[13px] md:table">
            <thead className="border-b border-border bg-subtle text-[12px] text-muted">
              <tr>
                <th className="px-5 py-2.5 font-medium">Time</th>
                <th className="px-3 py-2.5 font-medium">Wallet</th>
                <th className="px-3 py-2.5 font-medium">Destination</th>
                <th className="px-3 py-2.5 font-medium">Amount</th>
                <th className="px-3 py-2.5 font-medium">Verdict</th>
                <th className="px-3 py-2.5 font-medium">Path</th>
                <th className="px-5 py-2.5 text-right font-medium">Duration</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {ordered.map((r) => (
                <tr key={r.key} data-testid="request-row" data-verdict={r.check.verdict}
                  onClick={() => go(`/requests/${encodeURIComponent(r.key)}`)}
                  className="row-link cursor-pointer">
                  <td className="px-5 py-3 tabular-nums text-muted">{time(r.ts)}</td>
                  <td className="px-3 py-3"><Addr a={r.received?.from} labels={r.run.scenario.labels} /></td>
                  <td className="px-3 py-3"><Addr a={r.check.decoded?.destination} labels={r.run.scenario.labels} /></td>
                  <td className="px-3 py-3 text-ink">{amountText(r)}</td>
                  <td className="px-3 py-3"><VerdictPill v={r.check.verdict} /></td>
                  <td className="px-3 py-3 text-muted">{PATH_WORD[r.check.path] ?? r.check.path}</td>
                  <td className="px-5 py-3 text-right tabular-nums text-muted">
                    <a href={`#/requests/${encodeURIComponent(r.key)}`} className="text-muted no-underline"
                      onClick={(e) => e.stopPropagation()}>{ms(r.check.timings_ms?.total)}</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {/* phones: list */}
          <ul className="divide-y divide-border md:hidden">
            {ordered.map((r) => (
              <li key={r.key}>
                <a href={`#/requests/${encodeURIComponent(r.key)}`} data-testid="request-row-m" data-verdict={r.check.verdict}
                  className="row-link block px-4 py-3 text-body no-underline">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-ink">{amountText(r)}</span>
                    <VerdictPill v={r.check.verdict} />
                  </div>
                  <div className="mt-1 text-[12px] text-muted">
                    to <Addr a={r.check.decoded?.destination} labels={r.run.scenario.labels} />
                  </div>
                  <div className="mt-0.5 text-[12px] text-faint">
                    {time(r.ts)} · {PATH_WORD[r.check.path] ?? r.check.path} · {ms(r.check.timings_ms?.total)}
                  </div>
                </a>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
