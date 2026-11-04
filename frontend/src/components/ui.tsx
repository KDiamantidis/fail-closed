import type { ReactNode } from "react";
import { short, type Verdict } from "../lib/api";

export const VERDICT_STYLE: Record<Verdict, { text: string; border: string; bg: string; label: string }> = {
  ALLOW: { text: "text-signal", border: "border-signal/50", bg: "bg-signal/10", label: "ALLOW" },
  DELAY: { text: "text-hold", border: "border-hold/50", bg: "bg-hold/10", label: "DELAY" },
  NEEDS_HUMAN: { text: "text-human", border: "border-human/50", bg: "bg-human/10", label: "NEEDS HUMAN" },
  BLOCK: { text: "text-deny", border: "border-deny/60", bg: "bg-deny/10", label: "BLOCK" },
};

export function VerdictPill({ v, big = false }: { v: Verdict; big?: boolean }) {
  const s = VERDICT_STYLE[v] ?? VERDICT_STYLE.BLOCK;
  return (
    <span data-verdict={v}
      className={`inline-flex items-center rounded border font-bold tracking-wider ${s.text} ${s.border} ${s.bg} ${big ? "px-2.5 py-1 text-[13px]" : "px-1.5 py-0.5 text-[11px]"}`}>
      {v === "BLOCK" ? "✕ " : ""}{s.label}
    </span>
  );
}

export function Section({ id, cmd, title, children, aside }:
  { id: string; cmd: string; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="scroll-mt-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={`${id}-h`} className="text-[15px] font-bold text-fg">
          <span className="text-signal">$ </span>{cmd}
          <span className="ml-3 font-normal text-faint">// {title}</span>
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** An address, shortened, with the scenario label if there is one. Full value on hover. */
export function Addr({ a, labels }: { a?: string | null; labels?: Record<string, string> }) {
  if (!a) return null;
  const label = labels?.[a.toLowerCase()];
  return (
    <span title={a} className="hex">
      <span className="text-fg">{short(a)}</span>
      {label ? <span className="text-faint"> ({label})</span> : null}
    </span>
  );
}

export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="w-20 shrink-0 text-faint">{k}</span>
      <span className="min-w-0 hex">{children}</span>
    </div>
  );
}
