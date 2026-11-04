import type { ReactNode } from "react";
import { Addr, Card, PageHeader, Pill, verdictTone } from "../components/ui";
import { eth, hexToInt, humanize, ms, short } from "../lib/api";
import { headline, PATH_WORD, pathWords, rank, REPLAY_TITLE, ruleReason } from "../lib/copy";
import { explanationFor, type Row } from "../lib/store";
import { amountText } from "./Requests";

const BANNER: Record<string, string> = {
  ok: "border-ok-line bg-ok-bg text-ok",
  held: "border-held-line bg-held-bg text-held",
  review: "border-review-line bg-review-bg text-review",
  bad: "border-bad-line bg-bad-bg text-bad",
  neutral: "border-border bg-subtle text-ink",
  brand: "border-border bg-subtle text-ink",
};

function Meta({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="label">{k}</dt>
      <dd className="mt-0.5 text-ink">{children}</dd>
    </div>
  );
}

function Timeline({ row }: { row: Row }) {
  const ev = row.run.events.filter((e) => e.check_id === row.check.check_id);
  const t0 = ev.find((e) => e.type === "received")?.ts;
  const at = (type: string) => ev.find((e) => e.type === type)?.ts;
  const fast = row.check.path !== "slow";
  const steps = [
    { label: "Received", ts: at("received") },
    { label: "Rules checked", ts: at("request_rules") },
    { label: "Sandbox", ts: at("sandbox_done"), skipped: fast, note: fast ? "not needed" : undefined },
    { label: "Verdict", ts: at("verdict") },
    { label: "Signer", ts: at("signer") },
  ];
  return (
    <ol className="grid grid-cols-1 gap-2 sm:grid-cols-5 sm:gap-1" data-testid="timeline">
      {steps.map((s, i) => {
        const done = s.ts != null;
        return (
          <li key={s.label} className="flex min-w-0 items-center gap-3 sm:block">
            <div className={`size-2 shrink-0 rounded-full sm:h-1 sm:w-auto ${done ? "bg-brand" : "bg-border"}`} />
            <p className={`flex-1 truncate text-[12px] font-medium sm:mt-2 ${done ? "text-ink" : "text-faint"}`}>{s.label}</p>
            <p className="text-[12px] tabular-nums text-faint">
              {s.skipped ? s.note : done && t0 != null ? (i === 0 ? "0 ms" : `+${Math.round((s.ts! - t0) * 1000)} ms`) : "…"}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function Diff({ diff, labels }: { diff: any; labels: Record<string, string> }) {
  const pre = diff?.pre ?? {};
  const post = diff?.post ?? {};
  const addrs = Object.keys(post);
  if (!addrs.length) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      {addrs.map((a) => (
        <div key={a} className="border-b border-border px-4 py-3 last:border-0">
          <Addr a={a} labels={labels} />
          <div className="mt-2 space-y-1.5">
            {"code" in post[a] && (() => {
              const after = String(post[a].code);
              const delegated = after.startsWith("0xef0100");
              return (
                <div data-testid={delegated ? "delegation-line" : undefined}
                  className={`rounded-md px-3 py-2 ${delegated ? "border border-bad-line bg-bad-bg" : "bg-subtle"}`}>
                  {delegated && (
                    <p className="mb-1 text-[13px] font-semibold text-bad">
                      Control handed over: this wallet would now run the code of{" "}
                      <Addr a={"0x" + after.slice(8)} labels={labels} />
                    </p>
                  )}
                  <p className="mono wrap-any text-body">code {pre[a]?.code ?? "0x"} → {after}</p>
                </div>
              );
            })()}
            {"nonce" in post[a] && (
              <p className="mono text-body">nonce {String(pre[a]?.nonce ?? 0)} → {String(post[a].nonce)}</p>
            )}
            {"balance" in post[a] && (() => {
              const b = hexToInt(pre[a]?.balance ?? "0x0"), c = hexToInt(post[a].balance);
              const d = c - b;
              return (
                <p className="mono text-body">
                  balance {eth(b)} → {eth(c)} <span className={d < 0n ? "text-bad" : "text-ok"}>({d < 0n ? "" : "+"}{eth(d)})</span>
                </p>
              );
            })()}
          </div>
        </div>
      ))}
    </div>
  );
}

function Probe({ probe, labels }: { probe: any[]; labels: Record<string, string> }) {
  if (!probe?.length) return null;
  const moved = probe.filter((p) => p.moved);
  return (
    <div className="mt-4">
      <p className="label">Probe after delegation</p>
      <p className="text-[13px] text-muted">
        The Guard called the delegated wallet as an outsider would ({probe.length} call{probe.length === 1 ? "" : "s"}) to see whether funds can leave.
      </p>
      {moved.length === 0 && <p className="mt-2 text-[13px] text-ok">No call moved funds.</p>}
      {moved.map((p, i) => (
        <div key={i} data-testid="probe-evidence" className="mt-2 rounded-md border border-bad-line bg-bad-bg px-3 py-2 text-[13px] text-bad">
          <span className="mono font-medium">{p.call}</span> moved{" "}
          {Object.entries(p.native_out ?? {}).map(([to, amt]) => (
            <span key={to}><b>{eth(amt as string)}</b> to <Addr a={to} labels={labels} /> </span>
          ))}
          {(p.token_out ?? []).map((t: any, j: number) => (
            <span key={j}>{eth(t.amount).replace(" ETH", "")} tokens of {short(t.token)} to <Addr a={t.to} labels={labels} /> </span>
          ))}
          <span className="block text-[12px] text-bad/80">Caller {short(p.caller)} ({p.caller_kind})</span>
        </div>
      ))}
    </div>
  );
}

function plainTitle(row: Row): string {
  const labels = row.run.scenario.labels ?? {};
  const d = row.check.decoded;
  const from = labels[String(row.received?.from ?? "").toLowerCase()] ?? short(row.received?.from);
  if (d?.kind === "delegation") return `Delegation request from the ${from}`;
  const to = d?.destination ? labels[d.destination.toLowerCase()] ?? short(d.destination) : "unknown";
  return `${amountText(row)} to ${to.startsWith("0x") ? to : `the ${to}`}`;
}

export function RequestDetail({ row }: { row: Row }) {
  const c = row.check;
  const labels = row.run.scenario.labels ?? {};
  const tone = verdictTone(c.verdict);
  const findings = (c.findings ?? []).filter((f: any) => f.action !== "ALLOW");
  const rules = [...new Set([...findings].sort((a: any, b: any) => rank(a) - rank(b)).map((f: any) => f.rule_id))] as string[];
  const signed = !!row.item.signer?.signed;
  const reason = String(row.item.signer?.reason ?? "").replace(/^refused:\s*/i, "").replace(/\s*\(no approval, no signature\)\s*$/i, "");
  const ai = explanationFor(row.run, c.check_id) ?? (c.llm ? { available: true, ...c.llm } : null);
  const held = (row.run.result?.delay_worker?.results ?? []).find((d: any) => d.id === c.check_id);
  const sim = c.simulation;

  return (
    <div data-testid="request-detail" data-verdict={c.verdict}>
      <a href={`#/requests?run=${row.run.runId}`} className="text-[13px]">← {REPLAY_TITLE[row.run.scenario.name] ?? "All requests"}</a>
      <div className="mt-3">
        <PageHeader title={plainTitle(row)} lead={<span className="text-[13px]">Replay step: {row.item.label}</span>} />
      </div>

      <div className={`rounded-xl border px-5 py-4 ${BANNER[tone]}`} data-testid="verdict-banner">
        <p className="text-[17px] font-semibold">{headline(c)}</p>
        <p className="mt-0.5 text-[13px] text-body">{signed ? "The signer signed it." : "Nothing was signed."}</p>
        {rules.length > 0 && (
          <p className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[12px] opacity-80">Rules</span>
            {rules.map((r) => <span key={r} className="tag" data-testid="rule-tag">{r}</span>)}
          </p>
        )}
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="min-w-0 space-y-6">
          <Card title="Request">
            <dl className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 sm:grid-cols-3">
              <Meta k="Wallet"><Addr a={row.received?.from} labels={labels} /></Meta>
              <Meta k="Destination"><Addr a={c.decoded?.destination} labels={labels} /></Meta>
              <Meta k="Amount">{amountText(row)}</Meta>
              <Meta k="Chain">{row.received?.chain_id ?? "–"}</Meta>
              <Meta k="Check">{PATH_WORD[c.path] ?? c.path}</Meta>
              <Meta k="Decision time">{ms(c.timings_ms?.total)}</Meta>
            </dl>
            <div className="mt-5"><Timeline row={row} /></div>
            <p className="mt-4 text-[13px] text-muted">
              <span className="font-medium text-body">Why this check: </span>{pathWords(c.path)}
            </p>
            {c.path_reasons?.length > 0 && (
              <details className="mt-1 text-[12px] text-faint">
                <summary className="cursor-pointer">Technical detail</summary>
                <p className="mt-1 wrap-any">{humanize(c.path_reasons.join("; "), labels)}</p>
              </details>
            )}
          </Card>

          {sim && (
            <Card title="Evidence from the sandbox" testid="evidence">
              {sim.ok ? (
                <>
                  <p className="mb-3 text-[13px] text-muted">
                    The request was run on a fresh, isolated copy of the chain. This is what really changes.
                  </p>
                  <Diff diff={sim.state_diff} labels={labels} />
                  <Probe probe={sim.probe} labels={labels} />
                </>
              ) : (
                <p className="text-[13px] text-bad">The sandbox check failed, so the request was blocked: {sim.error}</p>
              )}
            </Card>
          )}
          {findings.length > 0 && (
            <Card title="What the rules found">
              <ul className="space-y-3">
                {findings.map((f: any, i: number) => (
                  <li key={i} className="flex gap-3">
                    <span className="tag mt-0.5 h-fit">{f.rule_id}</span>
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-medium text-ink">
                        {ruleReason(f.rule_id, f.action).replace(/^./, (s) => s.toUpperCase())}
                        <Pill tone={verdictTone(f.action)}>{({ BLOCK: "Blocks", NEEDS_HUMAN: "Needs review", DELAY: "Holds" } as any)[f.action] ?? f.action}</Pill>
                      </p>
                      {f.detail && <p className="mt-0.5 text-[13px] text-muted wrap-any">{humanize(f.detail, labels)}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

        </div>

        <div className="space-y-6">
          <Card title="Signer outcome" testid="signer-card">
            <p data-testid="signer-result" className={`text-[18px] font-semibold ${signed ? "text-ok" : "text-bad"}`}>
              {signed ? "Signed" : `Signature refused: ${reason}`}
            </p>
            <p className="mt-1 text-[13px] text-muted">
              {signed ? "The Guard issued an approval bound to this exact request; the signer checked it and signed."
                : "The signer only signs with a valid Guard approval for this exact request."}
            </p>
            {(c.breaker ?? []).length > 0 && (
              <div className="mt-3 rounded-md border border-held-line bg-held-bg px-3 py-2 text-[13px] text-held">
                {c.breaker.map((b: string, i: number) => <p key={i}>{humanize(b, labels).replace(/^level (\d):/, "Breaker level $1:")}</p>)}
              </div>
            )}
            {held && (
              <div className="mt-3 rounded-md border border-border bg-subtle px-3 py-2 text-[13px]" data-testid="hold-outcome">
                <p className="font-medium text-ink">After the hold: {held.status === "released" ? "released and signed" : "not released"}</p>
                <p className="text-muted">{humanize(held.reason, labels)}</p>
                <p className="mt-1 text-[12px] text-faint">Demo: {row.run.result.delay_worker.skipped_minutes} min skipped</p>
              </div>
            )}
          </Card>

          <Card title="AI analysis" testid="ai-analysis"
            action={ai?.mock ? <Pill tone="held" testid="ai-mock">Mock</Pill> : ai?.available ? <Pill tone="ok">Live</Pill> : undefined}>
            <p className="mb-3 rounded-md bg-subtle px-3 py-2 text-[12px] text-muted">
              AI explains, it never decides. The verdict above comes from fixed rules.
            </p>
            {!ai && <p className="text-[13px] text-muted">Analysis in progress. It arrives after the verdict.</p>}
            {ai && !ai.available && <p className="text-[13px] text-muted">AI analysis is unavailable. The verdict is the same.</p>}
            {ai?.available !== false && ai && (
              <>
                <p className="label">Risk score</p>
                <div className="mt-1 flex items-center gap-3">
                  <div className="h-1.5 flex-1 rounded-full bg-border">
                    <div className="h-1.5 rounded-full bg-ink/70" style={{ width: `${Math.max(2, Math.min(100, ai.risk_score))}%` }} />
                  </div>
                  <span className="text-[13px] font-semibold tabular-nums text-ink">{ai.risk_score}/100</span>
                </div>
                <p className="mt-3 text-[13px] text-ink">{humanize(String(ai.intent_summary).replace(/^\[MOCK\]\s*/, ""), labels)}</p>
                <p className="mt-1 text-[13px] text-muted">{humanize(String(ai.explanation).replace(/^\[MOCK\]\s*/, ""), labels)}</p>
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
