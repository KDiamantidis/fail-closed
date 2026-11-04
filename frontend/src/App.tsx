import { useEffect, useState } from "react";
import { BlastRadius } from "./components/BlastRadius";
import { Breaker } from "./components/Breaker";
import { Containment } from "./components/Containment";
import { Header, type Status } from "./components/Header";
import { Load } from "./components/Load";
import { Pending } from "./components/Pending";
import { Scenarios, type RunState } from "./components/Scenarios";
import { Why } from "./components/Why";
import { getJSON } from "./lib/api";

const REPO = "https://github.com/KDiamantidis/fail-closed";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [mainBreaker, setMainBreaker] = useState<any>(null);
  const [run, setRun] = useState<RunState | null>(null);

  useEffect(() => {
    const load = () => {
      getJSON<Status>("/status").then(setStatus).catch(() => setStatus(null));
      getJSON("/breaker").then(setMainBreaker).catch(() => undefined);
    };
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, []);

  const result = run?.result;
  const labels = run?.scenario.labels ?? {};

  return (
    <>
      <Header status={status} />
      <main className="mx-auto max-w-[1120px] space-y-12 px-4 pb-16 sm:px-6">
        <Why />
        <Scenarios run={run} setRun={setRun} />
        <BlastRadius data={result?.blast_radius ?? []} labels={labels} />
        <Breaker state={result ? result.breaker : mainBreaker}
          source={result ? `your demo run (${run!.scenario.name.slice(0, 1)})` : "main state"} labels={labels} />
        <Pending runId={result ? run!.runId : null} initial={result?.pending ?? []} labels={labels} />
        <Containment publicRun={!!status?.redteam_public} known={status !== null} />
        <Load />
      </main>
      <footer className="border-t border-line bg-chrome">
        <div className="mx-auto flex max-w-[1120px] flex-wrap items-center justify-between gap-3 px-4 py-5 text-[12px] sm:px-6">
          <span className="text-faint">// nothing signs until the Guard says so</span>
          <nav className="flex flex-wrap gap-4">
            <a href={REPO} target="_blank" rel="noreferrer">GitHub</a>
            <a href={`${REPO}#how-it-works`} target="_blank" rel="noreferrer">How it works</a>
            <a href={`${REPO}#honest-limits`} target="_blank" rel="noreferrer">Honest limits</a>
          </nav>
        </div>
      </footer>
    </>
  );
}
