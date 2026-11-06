import { useEffect, useState } from "react";
import { BreakerLadder } from "../components/BreakerLadder";
import { Card, PageHeader, Pill } from "../components/ui";
import { getJSON, humanize } from "../lib/api";
import { REPLAY_TITLE } from "../lib/copy";
import { breakerLevel, latestBreaker, useStore } from "../lib/store";

export function BreakerPage() {
  const { runs } = useStore();
  const [main, setMain] = useState<any>(null);
  useEffect(() => { getJSON("/breaker").then(setMain).catch(() => setMain(null)); }, []);
  const lb = latestBreaker(runs);
  const state = lb?.state ?? main;
  const labels = runs.find((r) => r.scenario.name === lb?.source)?.scenario.labels ?? {};
  const level = breakerLevel(state);
  const resets = runs.filter((r) => r.scenario.name === lb?.source).flatMap((r) =>
    (r.result?.results ?? []).filter((x: any) => x.action));

  return (
    <>
      <PageHeader title="Circuit breaker"
        lead="When attacks pile up, the Guard narrows what it will approve, one step at a time. Each level stays on until an operator resets it, and every reset is logged." />
      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <Card title="Status"
          action={<Pill tone={level >= 3 ? "bad" : level ? "held" : "ok"} dot>
            {level ? `Level ${level} active` : "Normal operation"}
          </Pill>}>
          <p className="-mt-1 mb-4 text-[13px] text-muted">
            {lb ? `State after your replay: ${REPLAY_TITLE[lb.source] ?? lb.source}.` : "Live state of this Guard. Run a replay to see the breaker react."}
          </p>
          <BreakerLadder state={state} labels={labels} />
        </Card>
        <div className="space-y-6">
          <Card title="How it protects customers">
            <ul className="space-y-2.5 text-[13px] text-body">
              <li>Each level affects as little as possible: one address, one wallet, one chain.</li>
              <li>Safe mode needs two paused wallets or three evidence blocks on the same chain within an hour.</li>
              <li>A global stop needs two independent signals. One bad request can never stop everyone.</li>
              <li>Levels never lift on their own. A person resets them, and the reset is recorded.</li>
            </ul>
          </Card>
          {resets.length > 0 && (
            <Card title="Operator actions in this replay">
              <ul className="space-y-2 text-[13px]">
                {resets.map((x: any, i: number) => (
                  <li key={i}>
                    <p className="font-medium text-ink">{x.plain ?? x.label}</p>
                    <p className="text-muted">{humanize(`${String(x.action).replace(/_/g, " ")} by ${x.by}${x.done ? "" : " (nothing to reset)"}`, labels)}</p>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
