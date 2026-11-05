import { useEffect, useState } from "react";
import { Card, Empty, PageHeader, Pill } from "../components/ui";
import { getJSON } from "../lib/api";

const OUTCOME: Record<string, { tone: "ok" | "held" | "review" | "bad"; word: string }[]> = {
  BLOCK: [{ tone: "bad", word: "Blocked" }],
  NEEDS_HUMAN: [{ tone: "review", word: "Needs review" }],
  "NEEDS_HUMAN / BLOCK": [{ tone: "review", word: "Needs review" }, { tone: "bad", word: "Blocked" }],
  "DELAY / BLOCK": [{ tone: "held", word: "Held" }, { tone: "bad", word: "Blocked" }],
};

export function Policies() {
  const [p, setP] = useState<any>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { getJSON("/policies").then(setP).catch(() => setFailed(true)); }, []);

  return (
    <>
      <PageHeader title="Policies"
        lead="The active rules, read-only. Rules decide every verdict. The AI analysis explains requests; it can ask for a closer check, but it never approves, blocks or lowers a verdict." />
      {!p ? (failed ? <Empty title="Policies could not be loaded" /> : <p className="text-muted">Loading…</p>) : (
        <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
          <Card title="Signing rules" pad={false} testid="policies">
            <ul className="divide-y divide-border">
              {p.rules.map((r: any) => (
                <li key={r.id} className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-start sm:gap-4">
                  <span className="tag h-fit w-fit">{r.id}</span>
                  <p className="min-w-0 flex-1 text-ink">{r.text}</p>
                  <span className="flex shrink-0 gap-1.5">
                    {(OUTCOME[r.verdict] ?? []).map((o) => <Pill key={o.word} tone={o.tone}>{o.word}</Pill>)}
                  </span>
                </li>
              ))}
              <li className="px-5 py-3.5 text-[13px] text-muted">
                The strictest outcome wins. If any check fails or cannot finish, the request is blocked.
              </li>
            </ul>
          </Card>
          <div className="space-y-6">
            <Card title="Circuit breaker">
              <ol className="space-y-2 text-[13px]">
                {p.breaker.map((b: any) => (
                  <li key={b.level}><span className="font-medium text-ink">Level {b.level}.</span> <span className="text-body">{b.text}</span></li>
                ))}
              </ol>
            </Card>
            <Card title="Settings">
              <dl className="space-y-2 text-[13px]">
                <div className="flex justify-between gap-3"><dt className="text-muted">Hold period</dt><dd className="text-ink">{p.hold_minutes} min</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Known tokens</dt><dd className="text-ink">{p.lists.known_tokens}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Approved delegate contracts</dt><dd className="text-ink">{p.lists.delegate_allowlist}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Treasury allowlist</dt><dd className="text-ink">{p.lists.treasury_allowlist}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Sandbox time limit</dt><dd className="text-ink">{p.sandbox.timeout_seconds} s</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Sandbox checks at once</dt><dd className="text-ink">{p.sandbox.max_concurrent}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">AI may ask for a sandbox check</dt><dd className="text-ink">{p.ai.escalation_on_fast_path ? "Yes" : "No"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">AI asked above</dt><dd className="text-ink" data-testid="policy-ai-pct">{p.ai.ask_above_pct}% of the wallet balance, or a new destination</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">AI checks at once</dt><dd className="text-ink">{p.ai.max_concurrent}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">AI check time limit</dt><dd className="text-ink">{p.ai.fast_path_timeout_seconds} s, then full simulation</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">AI check model</dt><dd className="mono text-ink">{p.ai.fast_model}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Explanation model</dt><dd className="mono text-ink">{p.ai.explain_model}</dd></div>
              </dl>
              <p className="mt-3 text-[12px] text-muted">{p.ai.policy} AI explains, it never decides.</p>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
