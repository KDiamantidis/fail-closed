import { useEffect, useRef, type ReactNode } from "react";
import { useStore, type Status } from "../lib/store";
import { DemoNotice, GlobalStopBanner, LiveNote, RiskDot, RiskStrip } from "./Risk";
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

const VULTR_LINE = "Runs on Vultr: Serverless Inference (AI) · Cloud Compute (Guard + sandboxes)";

function aiChip(status: Status | null): string {
  if (!status) return "AI: …";
  if (status.llm === "MOCK") return "AI: Mock";
  if (status.llm === "OFF") return "AI: Off";
  return status.llm_budget?.reached ? "AI: Vultr · paused (daily budget)" : `AI: Vultr · ${status.model}`;
}

export function modelsLine(status: Status | null): string {
  if (!status || status.llm !== "REAL") return "AI: local mock, no model is called";
  return `checks: ${status.model} · explanations: ${status.explain_model ?? status.model}`;
}

export function Shell({ children }: { children: ReactNode }) {
  const { status, route, rows, runs, risk } = useStore();
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
  const tabs = useRef<HTMLElement>(null);
  useEffect(() => {  // phone: keep the active tab in view
    const a = tabs.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (a && tabs.current) tabs.current.scrollTo({ left: a.offsetLeft - tabs.current.clientWidth / 2 + a.clientWidth / 2 });
  }, [route]);

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-border bg-surface">
        <RiskStrip />
        <div className="flex items-center justify-between gap-3 px-4 py-3 lg:px-6">
          <a href="#/overview" className="flex items-center gap-2.5 text-ink no-underline" aria-label="Fail Closed, overview">
            <Logo />
            <span className="leading-tight">
              <span className="block text-[15px] font-semibold text-ink">Fail Closed</span>
              <span className="block text-[12px] font-medium text-muted">Signing Guard</span>
            </span>
          </a>
          <div className="flex flex-wrap items-center justify-end gap-1.5" aria-label="System status">
            <LiveNote />
            <Pill tone="brand" testid="env-badge">Environment: Demo</Pill>
            <span className="hidden flex-wrap gap-1.5 md:flex">
              <Pill testid="pill-sandbox">Sandbox: {status ? (status.sandbox_mode === "warm" ? "Warm pool" : "Fresh") : "…"}</Pill>
              <span title={modelsLine(status)}>
                <Pill tone={status?.llm === "REAL" ? "ok" : "held"} testid="pill-ai">{aiChip(status)}</Pill>
              </span>
            </span>
          </div>
        </div>
        {/* phones: status pills and the navigation as a scrollable row */}
        <div className="flex gap-1.5 overflow-x-auto px-4 pb-2 md:hidden">
          <Pill>Sandbox: {status?.sandbox_mode === "warm" ? "Warm" : "Fresh"}</Pill>
          <Pill tone={status?.llm === "REAL" ? "ok" : "held"}>{aiChip(status)}</Pill>
        </div>
        <div className="relative border-t border-border lg:hidden">
        <nav ref={tabs} className="flex gap-1 overflow-x-auto px-3 py-1.5 pr-10" aria-label="Sections" data-testid="phone-tabs">
          {nav.map((n) => (
            <a key={n.to} href={`#${n.to}`} data-testid={`nav-${n.icon}`} aria-current={active(n.to) ? "page" : undefined}
              className={`flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium no-underline ${active(n.to) ? "bg-brand-tint text-brand" : "text-body"}`}>
              {n.label}{n.count ? <span className="text-faint">{n.count}</span> : null}
              {n.icon === "breaker" && <RiskDot level={risk.level} />}
            </a>
          ))}
        </nav>
        {/* the tabs scroll: a fade on the right edge says so */}
        <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-surface to-transparent" aria-hidden />
        </div>
      </header>

      <div className="flex">
        <div className="hidden w-60 shrink-0 border-r border-border bg-surface lg:block">
        <aside className="sticky top-[93px] flex h-[calc(100dvh-93px)] flex-col px-3 py-4">
          <nav className="space-y-0.5" aria-label="Sections">
            {nav.map((n) => (
              <a key={n.to} href={`#${n.to}`} aria-current={active(n.to) ? "page" : undefined}
                className={`row-link flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium no-underline ${active(n.to) ? "bg-brand-tint text-brand" : "text-body"}`}>
                <Icon name={n.icon} />
                <span className="flex-1">{n.label}</span>
                {n.count ? <span className="rounded-full bg-subtle px-1.5 text-[11px] text-muted">{n.count}</span> : null}
                {n.icon === "breaker" && <RiskDot level={risk.level} />}
              </a>
            ))}
          </nav>
          <div className="mt-auto space-y-2 rounded-lg border border-border bg-subtle p-3 text-[12px] text-muted">
            <p>Demo environment. Every replay runs on its own isolated state, on a local test chain.</p>
            <p data-testid="vultr-sidebar">{VULTR_LINE}</p>
          </div>
        </aside>
        </div>
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <div className="mx-auto max-w-[1180px]"><DemoNotice /><GlobalStopBanner />{children}</div>
          <footer className="mx-auto mt-12 flex max-w-[1180px] flex-wrap items-center justify-between gap-3 border-t border-border pt-5 text-[13px] text-muted">
            <span>Fail Closed · No approval, no signature.<span className="block text-[12px]" data-testid="vultr-footer">{VULTR_LINE}</span></span>
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
