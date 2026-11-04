import type { ReactNode } from "react";
import { VERDICT_WORD } from "../lib/copy";
import { short } from "../lib/api";

type Tone = "ok" | "held" | "review" | "bad" | "neutral" | "brand";

const TONE: Record<Tone, string> = {
  ok: "text-ok bg-ok-bg border-ok-line",
  held: "text-held bg-held-bg border-held-line",
  review: "text-review bg-review-bg border-review-line",
  bad: "text-bad bg-bad-bg border-bad-line",
  neutral: "text-body bg-subtle border-border",
  brand: "text-brand bg-brand-tint border-[#99e6d4]",
};

const VERDICT_TONE: Record<string, Tone> = { ALLOW: "ok", DELAY: "held", NEEDS_HUMAN: "review", BLOCK: "bad", BUSY: "bad" };

export function Pill({ tone = "neutral", children, dot = false, testid }:
  { tone?: Tone; children: ReactNode; dot?: boolean; testid?: string }) {
  return (
    <span data-testid={testid}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[12px] font-medium whitespace-nowrap ${TONE[tone]}`}>
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export function VerdictPill({ v }: { v: string }) {
  return <Pill tone={VERDICT_TONE[v] ?? "neutral"} dot>{VERDICT_WORD[v as keyof typeof VERDICT_WORD] ?? v}</Pill>;
}

export function verdictTone(v: string): Tone {
  return VERDICT_TONE[v] ?? "neutral";
}

export function Card({ title, action, children, className = "", testid, pad = true }:
  { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; testid?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`} data-testid={testid}>
      {(title || action) && (
        <div className="card-head">
          {typeof title === "string" ? <h2 className="card-title">{title}</h2> : title}
          {action}
        </div>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, lead, action }: { title: string; lead?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold sm:text-[24px]">{title}</h1>
        {lead && <p className="mt-1 max-w-[720px] text-[14px] text-muted">{lead}</p>}
      </div>
      {action}
    </div>
  );
}

/** An address: monospace, shortened, with the scenario label. Full value on hover. */
export function Addr({ a, labels }: { a?: string | null; labels?: Record<string, string> }) {
  if (!a) return <span className="text-faint">—</span>;
  const label = labels?.[a.toLowerCase()];
  return (
    <span title={a} className="whitespace-nowrap">
      <span className="mono text-ink">{short(a)}</span>
      {label && <span className="text-muted"> · {label}</span>}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border-strong bg-subtle px-5 py-8 text-center">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mt-1 text-[13px] text-muted">{children}</div>}
    </div>
  );
}

export function Spinner() {
  return <span className="inline-block size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />;
}
