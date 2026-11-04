import { useEffect, useRef, useState } from "react";
import { ms } from "../lib/api";

/** Per-request decision time, by check type. Dot plot on a log scale: instant checks take
 * milliseconds, sandbox checks take seconds, and both must stay readable on one axis. */
const FAST = "var(--color-chart-fast)";
const SLOW = "var(--color-chart-slow)";
const H = 220, L = 52, R = 16, T = 18, B = 30;
const TICKS = [1, 10, 100, 1000, 10000];

export function LatencyChart({ rows, total = 51 }: { rows: any[]; total?: number }) {
  const [hover, setHover] = useState<any | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const maxMs = Math.max(1000, ...rows.map((r) => r.ms));
  const top = TICKS.find((t) => t >= maxMs) ?? 100000;
  const ticks = TICKS.filter((t) => t <= top);
  const lo = Math.log10(0.5), hi = Math.log10(top);
  const y = (v: number) => T + (H - T - B) * (1 - (Math.log10(Math.max(0.5, v)) - lo) / (hi - lo));
  const x = (i: number) => L + ((W - L - R) * (i + 0.5)) / total;
  const isSlow = (r: any) => r.path === "slow";
  const attack = rows.find((r) => r.attack);

  return (
    <figure className="m-0" data-testid="latency-chart">
      <figcaption className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-body">
        <span className="font-medium text-ink">Decision time per request</span>
        <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-full" style={{ background: FAST }} />Instant check</span>
        <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-full" style={{ background: SLOW }} />Sandbox check</span>
      </figcaption>
      <div className="relative" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block" role="img"
          aria-label="Decision time per request, in milliseconds, log scale" onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
              <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--color-muted)">
                {t >= 1000 ? `${t / 1000} s` : `${t} ms`}
              </text>
            </g>
          ))}
          <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke="var(--color-border-strong)" strokeWidth={1} />
          <text x={L} y={H - 8} fontSize="11" fill="var(--color-muted)">Request 1</text>
          <text x={W - R} y={H - 8} fontSize="11" fill="var(--color-muted)" textAnchor="end">{total}</text>
          {rows.map((r, n) => {
            const cx = x(r.i ?? n), cy = y(r.ms);
            return (
              <g key={r.i ?? n} onMouseEnter={() => setHover({ ...r, cx, cy })}>
                <circle cx={cx} cy={cy} r={10} fill="transparent" />
                <circle cx={cx} cy={cy} r={4.5} fill={isSlow(r) ? SLOW : FAST} stroke="var(--color-surface)" strokeWidth={2} />
              </g>
            );
          })}
          {attack && (
            <g pointerEvents="none">
              <circle cx={x(attack.i)} cy={y(attack.ms)} r={9} fill="none" stroke="var(--color-ink)" strokeWidth={1.5} />
              <text x={x(attack.i) + (attack.i > total * 0.7 ? -14 : 14)} y={y(attack.ms) + 4} fontSize="12" fontWeight={600}
                fill="var(--color-ink)" textAnchor={attack.i > total * 0.7 ? "end" : "start"}>
                Attack: {attack.verdict === "BLOCK" ? "blocked" : attack.verdict.toLowerCase()}
              </text>
            </g>
          )}
        </svg>
        {hover && (
          <div className="pointer-events-none absolute z-10 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] shadow-sm"
            style={{ left: `${(hover.cx / W) * 100}%`, top: `${(hover.cy / H) * 100}%`, transform: "translate(-50%, calc(-100% - 10px))" }}>
            <p className="font-medium text-ink">Request {hover.i + 1}{hover.attack ? " · attack" : ""}</p>
            <p className="text-muted">{isSlow(hover) ? "Sandbox check" : "Instant check"} · {ms(hover.ms)}</p>
          </div>
        )}
      </div>
      <details className="mt-2 text-[12px] text-muted">
        <summary className="cursor-pointer">Show as table</summary>
        <div className="mt-2 max-h-56 overflow-auto rounded-md border border-border">
          <table className="w-full text-left">
            <thead className="sticky top-0 bg-subtle"><tr><th className="px-3 py-1.5 font-medium">Request</th><th className="px-3 py-1.5 font-medium">Check</th><th className="px-3 py-1.5 font-medium">Verdict</th><th className="px-3 py-1.5 text-right font-medium">Time</th></tr></thead>
            <tbody className="divide-y divide-border">
              {rows.map((r, n) => (
                <tr key={r.i ?? n}><td className="px-3 py-1">{(r.i ?? n) + 1}{r.attack ? " (attack)" : ""}</td><td className="px-3 py-1">{isSlow(r) ? "Sandbox" : "Instant"}</td><td className="px-3 py-1">{r.verdict}</td><td className="px-3 py-1 text-right tabular-nums">{ms(r.ms)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
