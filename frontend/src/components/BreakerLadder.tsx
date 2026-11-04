import { humanize } from "../lib/api";
import { Addr, Pill } from "./ui";

export const LEVELS = [
  { n: 1, key: "frozen_destinations", name: "Freeze one address",
    words: "Nothing more is paid to that address. Everyone else is unaffected." },
  { n: 2, key: "paused_wallets", name: "Pause one wallet",
    words: "That wallet signs nothing until a person has looked at it." },
  { n: 3, key: "safe_mode_chains", name: "Safe mode on one chain",
    words: "Every request on that chain waits for a person, and is still checked." },
  { n: 4, key: "global_stop", name: "Global stop",
    words: "No approvals at all. Only with two independent signals." },
];

export function BreakerLadder({ state, labels, compact = false }:
  { state: any | null; labels: Record<string, string>; compact?: boolean }) {
  return (
    <ol className="space-y-2" data-testid="breaker">
      {LEVELS.map((l) => {
        const v = state?.[l.key];
        const items: any[] = l.key === "global_stop" ? (v ? [v] : []) : (v ?? []);
        const on = items.length > 0;
        return (
          <li key={l.n} data-level={l.n} data-on={on}
            className={`rounded-lg border px-4 py-3 ${on ? (l.n >= 3 ? "border-bad-line bg-bad-bg" : "border-held-line bg-held-bg") : "border-border bg-surface"}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold text-ink">
                  <span className="mr-2 text-muted">Level {l.n}</span>{l.name}
                </p>
                {!compact && <p className="mt-0.5 text-[13px] text-muted">{l.words}</p>}
              </div>
              {on ? <Pill tone={l.n >= 3 ? "bad" : "held"} dot>Active</Pill> : <Pill>Off</Pill>}
            </div>
            {on && (
              <ul className="mt-2 space-y-1 text-[13px]">
                {items.map((it, i) => (
                  <li key={i}>
                    {l.key === "frozen_destinations" && <Addr a={it.address} labels={labels} />}
                    {l.key === "paused_wallets" && <Addr a={it.wallet} labels={labels} />}
                    {l.key === "safe_mode_chains" && <span className="font-medium text-ink">Chain {it.chain_id}</span>}
                    {l.key === "global_stop" && <span className="font-medium text-ink">{(it.signals ?? []).join(" + ")}</span>}
                    {!compact && <span className="block text-muted">{humanize(it.reason, labels)}</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}
