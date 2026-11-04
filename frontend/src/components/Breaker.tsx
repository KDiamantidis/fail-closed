import { Addr, Section } from "./ui";

const LEVELS = [
  { n: 1, name: "Freeze one address", words: "Nothing more goes to it.", key: "frozen_destinations" },
  { n: 2, name: "Pause one wallet", words: "It signs nothing until a human looks.", key: "paused_wallets" },
  { n: 3, name: "Safe mode on one chain", words: "No fast path; every request there waits for a human.", key: "safe_mode_chains" },
  { n: 4, name: "Global stop", words: "No approvals at all. Needs 2 independent signals.", key: "global_stop" },
];

export function Breaker({ state, source, labels }:
  { state: any | null; source: string; labels: Record<string, string> }) {
  return (
    <Section id="breaker" cmd="breaker status" title="latched until a human resets it"
      aside={<span className="text-[12px] text-faint">{source}</span>}>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-testid="breaker">
        {LEVELS.map((l) => {
          const v = state?.[l.key];
          const items: any[] = l.key === "global_stop" ? (v ? [v] : []) : (v ?? []);
          const on = items.length > 0;
          return (
            <li key={l.n} className={`panel px-3 py-3 ${on ? (l.n >= 3 ? "border-deny/60" : "border-hold/50") : ""}`}
              data-level={l.n} data-on={on}>
              <p className="flex items-center justify-between">
                <span className={`font-bold ${on ? (l.n >= 3 ? "text-deny" : "text-hold") : "text-dim"}`}>L{l.n} {l.name}</span>
                <span className={`text-[11px] ${on ? "text-deny" : "text-faint"}`}>{on ? "ON" : "off"}</span>
              </p>
              <p className="mt-1 text-[12px] text-faint">{l.words}</p>
              {on && (
                <ul className="mt-2 space-y-1 text-[12px]">
                  {items.map((it, i) => (
                    <li key={i} className="hex">
                      {l.key === "frozen_destinations" && <Addr a={it.address} labels={labels} />}
                      {l.key === "paused_wallets" && <Addr a={it.wallet} labels={labels} />}
                      {l.key === "safe_mode_chains" && <span>chain {it.chain_id}</span>}
                      {l.key === "global_stop" && <span>{it.signals?.join(" + ")}</span>}
                      <span className="block text-faint">{it.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </Section>
  );
}
