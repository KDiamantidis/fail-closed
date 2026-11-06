import { useState, type FormEvent } from "react";
import { REPLAY_TITLE } from "../lib/copy";
import { RISK, riskSentence, type RiskLevel } from "../lib/risk";
import { useStore } from "../lib/store";

/** Never colour alone: each level has its own shape too (check, warning triangle, stop). */
export function RiskIcon({ level, className = "size-4" }: { level: RiskLevel; className?: string }) {
  const icon = RISK[level].icon;
  return (
    <svg viewBox="0 0 16 16" className={`${className} shrink-0`} fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {icon === "check" && <><circle cx="8" cy="8" r="6" /><path d="M5.5 8.2l1.7 1.7 3.3-3.4" /></>}
      {icon === "alert" && <><path d="M8 2.2l6.2 11H1.8z" /><path d="M8 6.5v3M8 11.4v.1" /></>}
      {icon === "stop" && <><path d="M5.5 1.8h5l3.7 3.7v5l-3.7 3.7h-5l-3.7-3.7v-5z" /><path d="M5.6 8h4.8" /></>}
    </svg>
  );
}

/** The thin strip at the top of every page: level name, icon, one plain sentence. */
export function RiskStrip() {
  const { risk } = useStore();
  const m = RISK[risk.level];
  return (
    <div role="status" aria-live="polite" data-testid="risk-strip" data-level={risk.level}
      className={`risk-fade border-b ${m.strip}`}>
      <div className="flex min-w-0 items-start gap-2 px-4 py-1.5 text-[12.5px] font-medium sm:items-center lg:px-6">
        <RiskIcon level={risk.level} className="mt-px size-3.5 sm:mt-0" />
        <span className="min-w-0 flex-1" data-testid="risk-sentence">{riskSentence(risk.state, risk.level)}</span>
        <span className="hidden shrink-0 opacity-90 md:inline">
          {risk.source ? `Latest replay: ${REPLAY_TITLE[risk.source] ?? risk.source}` : "This Guard"} · level {risk.level} of 4
        </span>
      </div>
    </div>
  );
}

/** The sidebar mark next to "Circuit breaker": a coloured dot and the level number. */
export function RiskDot({ level }: { level: RiskLevel }) {
  return (
    <span className="flex items-center gap-1 text-[11px] font-semibold tabular-nums text-muted"
      data-testid="nav-breaker-level" data-level={level} role="img" aria-label={`Risk level ${level}: ${RISK[level].name}`}
      title={`Risk level ${level}: ${RISK[level].name}`}>
      <span className={`risk-fade size-2 rounded-full ${RISK[level].dot}`} aria-hidden />
      {level}
    </span>
  );
}

/** Overview KPI card, tinted by level. */
export function RiskKpi() {
  const { risk } = useStore();
  const m = RISK[risk.level];
  const detail = riskSentence(risk.state, risk.level).split(": ").slice(1).join(": ");
  return (
    <div className={`risk-fade card col-span-2 px-4 py-3 ${m.card}`} data-testid="kpi-risk" data-level={risk.level}>
      <p className={`text-[12px] font-medium ${risk.level ? m.text : "text-muted"}`}>Risk level</p>
      <p className={`mt-1 flex items-center gap-2 text-[24px] font-semibold leading-tight ${m.text}`}>
        <RiskIcon level={risk.level} className="size-5" />
        <span data-testid="kpi-risk-name">{m.name}</span>
        <span className={`text-[13px] font-medium ${risk.level ? m.text : "text-muted"}`}>level {risk.level} of 4</span>
      </p>
      <p className={`mt-0.5 text-[12px] ${risk.level ? m.text : "text-faint"}`}>{detail}</p>
    </div>
  );
}

/** Level 4 only: the deep red banner, with the admin action that lifts it. The admin token is
 * typed by an operator, sent once in a header, and never stored. */
/** Public "Reset demo": this visitor's demo back to level 0. Not the real lift. */
export function ResetDemoButton({ className = "btn btn-secondary", label = "Reset demo" }:
  { className?: string; label?: string }) {
  const { resetDemo, runs, running } = useStore();
  return (
    <button type="button" className={className} data-testid="reset-demo" disabled={!runs.length || running}
      onClick={() => resetDemo("button")}>
      {label}
    </button>
  );
}

export function DemoNotice() {
  const { notice } = useStore();
  if (!notice) return null;
  return <p role="status" data-testid="demo-notice"
    className="mb-4 rounded-lg border border-border bg-surface px-4 py-2.5 text-[13px] text-body">{notice}</p>;
}

export function GlobalStopBanner() {
  const { risk, liftBreaker, status } = useStore();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (risk.level !== 4) return null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    const err = await liftBreaker(token);
    setToken("");
    setBusy(false);
    setError(err);
  };

  return (
    <section role="alert" data-testid="global-stop-banner"
      className="risk-fade mb-6 rounded-xl border border-risk4-line bg-risk4-bg px-5 py-4 text-white">
      <div className="flex items-start gap-3">
        <RiskIcon level={4} className="mt-0.5 size-5" />
        <div className="min-w-0 flex-1">
          <p className="text-[16px] font-semibold">Global stop: no withdrawals are being signed</p>
          <p className="mt-0.5 text-[13px]">
            Two independent attack signals stopped every approval{risk.source ? " in this replay" : ""}. After a
            review, an operator lifts it: every level returns to 0 (Normal) and each reset is logged.
          </p>
          {risk.source && (
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
              <ResetDemoButton className="btn border-white/80 bg-transparent text-white" />
              <span className="text-[12.5px]">
                {status?.public_demo ? "Public demo: anyone can reset their own demo. " : ""}
                The real lift below needs the admin token.
              </span>
            </div>
          )}
          <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={submit}>
            <label className="flex min-w-0 flex-col gap-1 text-[12px] font-medium">
              Admin token
              <input type="password" autoComplete="off" spellCheck={false} value={token} data-testid="admin-token"
                onChange={(e) => setToken(e.target.value)}
                className="w-56 max-w-full rounded-md border border-white/60 bg-white px-2.5 py-1.5 text-[13px] text-ink" />
            </label>
            <button type="submit" disabled={!token || busy} data-testid="lift-global-stop"
              className="btn border-white bg-white text-risk4-bg">
              {busy ? "Lifting…" : "Lift global stop"}
            </button>
          </form>
          {error && <p className="mt-2 text-[13px] font-medium" data-testid="lift-error">Not lifted: {error}</p>}
        </div>
      </div>
    </section>
  );
}
