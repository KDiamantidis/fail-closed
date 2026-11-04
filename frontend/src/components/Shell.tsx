import type { ReactNode } from "react";
import { breakerLevel, latestBreaker, useStore } from "../lib/store";
import { Pill } from "./ui";

const ICONS: Record<string, ReactNode> = {
  overview: <path d="M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z" />,
  requests: <path d="M3 4h10M3 8h10M3 12h6" />,
  review: <path d="M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM8 5v3.2l2 1.3" />,
  breaker: <path d="M8 2v5M4.6 4.2a5 5 0 1 0 6.8 0" />,
  containment: <path d="M8 2l5 2v4c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4l5-2z" />,
  simulation: <path d="M2.5 13.5h11M4.5 11V8M7.5 11V5M10.5 11V7" />,
  policies: <path d="M4 2.5h6l2.5 2.5v8.5H4zM6.5 7h4M6.5 9.5h4" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 16 16" className="size-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>{ICONS[name]}</svg>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 24 24" className="size-7 shrink-0" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#0f766e" />
      <path d="M12 5.5l5 2v4.2c0 3.1-2.1 5.4-5 6.3-2.9-.9-5-3.2-5-6.3V7.5l5-2z" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M9.6 11.8l1.7 1.7 3.2-3.4" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const LEVEL_WORD = ["Normal", "Address frozen", "Wallet paused", "Chain in safe mode", "Global stop"];

export function Shell({ children }: { children: ReactNode }) {
  const { status, route, rows, runs } = useStore();
  const pendingReview = runs.reduce((n, r) => n + (r.result?.pending?.length ?? 0), 0);
  const nav = [
    { to: "/overview", label: "Overview", icon: "overview" },
    { to: "/requests", label: "Signing requests", icon: "requests", count: rows.length || undefined },
    { to: "/review", label: "Review queue", icon: "review", count: pendingReview || undefined },
    { to: "/breaker", label: "Circuit breaker", icon: "breaker" },
    { to: "/containment", label: "Containment", icon: "containment" },
    { to: "/simulation", label: "Simulation", icon: "simulation" },
    { to: "/policies", label: "Policies", icon: "policies" },
  ];
  const active = (to: string) => route === to || route.startsWith(to + "/");
  const lb = latestBreaker(runs);
  const level = lb ? breakerLevel(lb.state) : status?.breaker_level ?? 0;

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-border bg-surface">
        <div className="flex items-center justify-between gap-3 px-4 py-3 lg:px-6">
          <a href="#/overview" className="flex items-center gap-2.5 text-ink no-underline" aria-label="Fail Closed, overview">
            <Logo />
            <span className="leading-tight">
              <span className="block text-[15px] font-semibold text-ink">Fail Closed</span>
              <span className="block text-[12px] font-medium text-muted">Signing Guard</span>
            </span>
          </a>
          <div className="flex flex-wrap items-center justify-end gap-1.5" aria-label="System status">
            <Pill tone="brand" testid="env-badge">Environment: Demo</Pill>
            <span className="hidden flex-wrap gap-1.5 md:flex">
              <Pill testid="pill-sandbox">Sandbox: {status ? (status.sandbox_mode === "warm" ? "Warm pool" : "Fresh") : "…"}</Pill>
              <Pill tone={status?.llm === "REAL" ? "ok" : "held"} testid="pill-ai">
                AI analysis: {status ? (status.llm === "REAL" ? status.model : status.llm === "MOCK" ? "Mock" : "Off") : "…"}
              </Pill>
              <Pill tone={level >= 3 ? "bad" : level > 0 ? "held" : "ok"} dot testid="pill-breaker">
                Breaker: level {level} · {LEVEL_WORD[level]}
              </Pill>
            </span>
          </div>
        </div>
        {/* phones: status pills and the navigation as a scrollable row */}
        <div className="flex gap-1.5 overflow-x-auto px-4 pb-2 md:hidden">
          <Pill>Sandbox: {status?.sandbox_mode === "warm" ? "Warm" : "Fresh"}</Pill>
          <Pill tone={status?.llm === "REAL" ? "ok" : "held"}>AI: {status?.llm === "REAL" ? status.model : status?.llm === "OFF" ? "Off" : "Mock"}</Pill>
          <Pill tone={level >= 3 ? "bad" : level > 0 ? "held" : "ok"} dot>Breaker L{level}</Pill>
        </div>
        <nav className="flex gap-1 overflow-x-auto border-t border-border px-3 py-1.5 lg:hidden" aria-label="Sections">
          {nav.map((n) => (
            <a key={n.to} href={`#${n.to}`} data-testid={`nav-${n.icon}`}
              className={`flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium no-underline ${active(n.to) ? "bg-brand-tint text-brand" : "text-body"}`}>
              {n.label}{n.count ? <span className="text-faint">{n.count}</span> : null}
            </a>
          ))}
        </nav>
      </header>

      <div className="flex">
        <div className="hidden w-60 shrink-0 border-r border-border bg-surface lg:block">
        <aside className="sticky top-[61px] flex h-[calc(100dvh-61px)] flex-col px-3 py-4">
          <nav className="space-y-0.5" aria-label="Sections">
            {nav.map((n) => (
              <a key={n.to} href={`#${n.to}`} aria-current={active(n.to) ? "page" : undefined}
                className={`row-link flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium no-underline ${active(n.to) ? "bg-brand-tint text-brand" : "text-body"}`}>
                <Icon name={n.icon} />
                <span className="flex-1">{n.label}</span>
                {n.count ? <span className="rounded-full bg-subtle px-1.5 text-[11px] text-muted">{n.count}</span> : null}
              </a>
            ))}
          </nav>
          <div className="mt-auto rounded-lg border border-border bg-subtle p-3 text-[12px] text-muted">
            Demo environment. Every replay runs on its own isolated state, on a local test chain.
          </div>
        </aside>
        </div>
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <div className="mx-auto max-w-[1180px]">{children}</div>
          <footer className="mx-auto mt-12 flex max-w-[1180px] flex-wrap items-center justify-between gap-3 border-t border-border pt-5 text-[13px] text-muted">
            <span>Fail Closed · No approval, no signature.</span>
            <span className="flex gap-4">
              <a href="https://github.com/KDiamantidis/fail-closed" target="_blank" rel="noreferrer">GitHub</a>
              <a href="https://github.com/KDiamantidis/fail-closed#how-it-works" target="_blank" rel="noreferrer">How it works</a>
              <a href="https://github.com/KDiamantidis/fail-closed#honest-limits" target="_blank" rel="noreferrer">Honest limits</a>
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
