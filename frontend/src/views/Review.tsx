import { Addr, Card, Empty, PageHeader, Pill, VerdictPill } from "../components/ui";
import { eth, humanize } from "../lib/api";
import { REPLAY_TITLE, ruleReason } from "../lib/copy";
import { useStore } from "../lib/store";

function clock(sec: number) {
  return new Date(sec * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function Review() {
  const { runs, rows, decide } = useStore();

  const needs = runs.filter((r) => r.result).flatMap((run) => {
    const pending: any[] = run.result.pending ?? [];
    const decided = Object.entries(run.decisions ?? {});
    return [
      ...pending.map((p) => ({ run, p, outcome: null as any })),
      ...decided.map(([id, outcome]) => ({ run, p: { id, ...(outcome.request ? outcome : {}) }, outcome })),
    ];
  });
  const original = (runId: string, id: string) => rows.find((r) => r.run.runId === runId && r.check.check_id === id);
  const delays = rows.filter((r) => r.check.verdict === "DELAY");

  return (
    <>
      <PageHeader title="Review queue"
        lead="Requests the rules did not approve on their own. A person decides on large or unusual payouts; held requests are released automatically after a waiting period. Before anything is signed, the rules are checked again." />

      <Card title="Needs review" testid="review-queue"
        action={<span className="hidden text-[12px] text-muted sm:block">Demo replays only. The live queue requires an operator token.</span>}>
        <p className="-mt-1 mb-3 text-[12px] text-muted sm:hidden">Demo replays only. The live queue requires an operator token.</p>
        {needs.length === 0 ? (
          <Empty title="Nothing waiting for review">
            Run <a href="#/overview">Honest large withdrawal</a> to see a request that needs a person.
          </Empty>
        ) : (
          <ul className="divide-y divide-border">
            {needs.map(({ run, p, outcome }) => {
              const row = original(run.runId, p.id);
              const labels = run.scenario.labels ?? {};
              const req = p.request ?? row?.received ?? {};
              const rules: string[] = p.rule_ids ?? (row?.check.findings ?? []).filter((f: any) => f.action === "NEEDS_HUMAN").map((f: any) => f.rule_id);
              return (
                <li key={`${run.runId}.${p.id}`} className="py-4 first:pt-0 last:pb-0" data-testid="review-item">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-2">
                        <span className="text-[15px] font-semibold text-ink">{row ? row.item.label : "Held request"}</span>
                        {!outcome ? <VerdictPill v="NEEDS_HUMAN" /> : outcome.signed ? <Pill tone="ok" dot>Approved and signed</Pill> : <Pill tone="bad" dot>Not signed</Pill>}
                      </p>
                      <p className="mt-1 text-[13px] text-body">
                        {eth(req.value ?? "0")} from <Addr a={req.from} labels={labels} /> to <Addr a={req.to} labels={labels} />
                      </p>
                      <p className="mt-1 text-[13px] text-muted">
                        Why: {rules.length ? rules.map((r) => ruleReason(r, "NEEDS_HUMAN")).join("; ") : humanize(p.reasons?.[0] ?? "", labels)}
                        {rules.map((r) => <span key={r} className="tag ml-1.5">{r}</span>)}
                      </p>
                      <p className="mt-1 text-[12px] text-faint">{REPLAY_TITLE[run.scenario.name] ?? run.scenario.name}</p>
                    </div>
                    {!outcome ? (
                      <div className="flex shrink-0 gap-2">
                        <button type="button" className="btn btn-primary" data-testid="approve"
                          onClick={() => decide(run.runId, p.id, "approve")}>Approve</button>
                        <button type="button" className="btn btn-danger" data-testid="reject"
                          onClick={() => decide(run.runId, p.id, "reject")}>Reject</button>
                      </div>
                    ) : null}
                  </div>
                  {outcome && (
                    <div data-testid="decision" data-status={outcome.status}
                      className={`mt-3 rounded-lg border px-4 py-3 text-[13px] ${outcome.signed ? "border-ok-line bg-ok-bg" : "border-border bg-subtle"}`}>
                      <p className={`font-semibold ${outcome.signed ? "text-ok" : "text-ink"}`}>
                        {outcome.status === "approved" && "Approved. The rules were checked again and found nothing new. Signed."}
                        {outcome.status === "blocked" && "Not released: something new appeared since the hold. Blocked, not signed."}
                        {outcome.status === "rejected" && "Rejected. Not signed."}
                        {outcome.status === "error" && `Could not record the decision: ${outcome.reason}`}
                      </p>
                      {outcome.status === "blocked" && outcome.reason && (
                        <p className="mt-0.5 text-muted">{humanize(outcome.reason, labels)}</p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="Held" className="mt-6" testid="held-list">
        <p className="-mt-1 mb-3 text-[13px] text-muted">
          Held requests wait, then are released automatically if nothing new has appeared. In the demo the wait is skipped so you can see the result.
        </p>
        {delays.length === 0 ? (
          <Empty title="Nothing held">The multi-chain replays hold the second and third chain.</Empty>
        ) : (
          <ul className="divide-y divide-border">
            {delays.map((r) => {
              const w = (r.run.result?.delay_worker?.results ?? []).find((d: any) => d.id === r.check.check_id);
              const labels = r.run.scenario.labels ?? {};
              return (
                <li key={r.key} className="flex flex-col gap-2 py-3 md:flex-row md:items-center md:justify-between">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2">
                      <a href={`#/requests/${encodeURIComponent(r.key)}`} className="font-medium text-ink no-underline">{r.item.label}</a>
                      <VerdictPill v="DELAY" />
                    </p>
                    <p className="mt-0.5 text-[13px] text-muted">
                      to <Addr a={r.check.decoded?.destination} labels={labels} />
                      {r.check.release_at ? <> · release at {clock(r.check.release_at)}</> : null}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {w ? (
                      w.status === "released"
                        ? <Pill tone="ok" dot>Released and signed</Pill>
                        : <Pill tone="bad" dot>Not released: blocked</Pill>
                    ) : <Pill>Waiting</Pill>}
                    <Pill testid="skipped-label">Demo: {r.run.result?.delay_worker?.skipped_minutes ?? 15} min skipped</Pill>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
