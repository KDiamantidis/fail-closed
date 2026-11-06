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

// Active levels use the same risk tints as the strip (level 4 stays light here; the banner is the deep red).
const ACTIVE: Record<number, string> = { 1: "border-risk1-line bg-risk1-bg", 2: "border-risk2-line bg-risk2-bg",
  3: "border-risk3-line bg-risk3-bg", 4: "border-risk3-line bg-risk3-bg" };
const ACTIVE_TEXT: Record<number, string> = { 1: "text-risk1", 2: "text-risk2", 3: "text-risk3", 4: "text-risk3" };

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
            className={`risk-fade rounded-lg border px-4 py-3 ${on ? ACTIVE[l.n] : "border-border bg-surface"}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold text-ink">
                  <span className="mr-2 text-muted">Level {l.n}</span>{l.name}
                </p>
                {!compact && <p className="mt-0.5 text-[13px] text-muted">{l.words}</p>}
              </div>
              {on ? <span className={`inline-flex items-center gap-1.5 rounded-full border border-current px-2 py-0.5 text-[12px] font-medium ${ACTIVE_TEXT[l.n]}`}>
                <span className="size-1.5 rounded-full bg-current" aria-hidden />Active</span> : <Pill>Off</Pill>}
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
