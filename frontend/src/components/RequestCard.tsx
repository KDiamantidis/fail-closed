import { eth, hexToInt, humanize, ms, short } from "../lib/api";
import { Addr, VerdictPill } from "./ui";

const FIELD_ORDER = ["code", "nonce", "balance"];

function DiffView({ diff, labels }: { diff: any; labels: Record<string, string> }) {
  const pre = diff?.pre ?? {};
  const post = diff?.post ?? {};
  const addrs = Object.keys(post);
  if (!addrs.length) return null;
  return (
    <div className="mt-3">
      <p className="mb-1 text-[12px] text-faint">state diff (what really changes)</p>
      <div className="overflow-hidden rounded border border-line bg-ink/70 text-[12px]">
        {addrs.map((a) => (
          <div key={a} className="border-b border-line/60 px-3 py-2 last:border-0">
            <Addr a={a} labels={labels} />
            {FIELD_ORDER.filter((f) => f in post[a]).map((f) => {
              const before = pre[a]?.[f];
              const after = post[a][f];
              if (f === "code") {
                const delegated = typeof after === "string" && after.startsWith("0xef0100");
                return (
                  <div key={f} data-testid={delegated ? "delegation-line" : undefined}
                    className={`mt-1 rounded px-2 py-1 hex ${delegated ? "border border-deny/60 bg-deny/10 text-deny" : "text-dim"}`}>
                    code {before ?? "0x"} → {after}
                    {delegated && (
                      <div className="mt-0.5 font-bold">
                        EIP-7702 delegation: this wallet now runs the code of <Addr a={"0x" + after.slice(8)} labels={labels} />
                      </div>
                    )}
                  </div>
                );
              }
              if (f === "nonce") {
                return <div key={f} className="mt-1 pl-2 text-dim">nonce {String(before ?? 0)} → {String(after)}</div>;
              }
              const b = hexToInt(before ?? "0x0"), c = hexToInt(after);
              const d = c - b;
              return (
                <div key={f} className="mt-1 pl-2 text-dim">
                  balance {eth(b)} → {eth(c)}{" "}
                  <span className={d < 0n ? "text-deny" : "text-signal"}>({d < 0n ? "" : "+"}{eth(d)})</span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function ProbeView({ probe, labels }: { probe: any[]; labels: Record<string, string> }) {
  if (!probe?.length) return null;
  const moved = probe.filter((p) => p.moved);
  return (
    <div className="mt-3 text-[12px]">
      <p className="mb-1 text-faint">probe after delegation: {probe.length} call{probe.length === 1 ? "" : "s"} from outside accounts</p>
      {moved.length === 0 && <p className="text-dim">no call moved funds</p>}
      {moved.map((p, i) => (
        <div key={i} data-testid="probe-evidence" className="rounded border border-deny/50 bg-deny/10 px-2 py-1.5 text-deny">
          {p.call} moved{" "}
          {Object.entries(p.native_out ?? {}).map(([to, amt]) => (
            <span key={to}>{eth(amt as string)} to <Addr a={to} labels={labels} /> </span>
          ))}
          {(p.token_out ?? []).map((t: any, j: number) => (
            <span key={j}>{eth(t.amount).replace(" ETH", "")} tokens of {short(t.token)} to <Addr a={t.to} labels={labels} /> </span>
          ))}
          <div className="text-deny/70">caller: <Addr a={p.caller} /> ({p.caller_kind})</div>
        </div>
      ))}
    </div>
  );
}

function Explanation({ ev, pending, labels }: { ev: any; pending: boolean; labels: Record<string, string> }) {
  if (!ev) {
    return pending ? <p className="mt-3 text-[12px] text-faint">AI explanation… (made after the verdict)</p> : null;
  }
  if (!ev.available) {
    return <p className="mt-3 text-[12px] text-faint">AI explanation unavailable. The verdict is the same.</p>;
  }
  return (
    <div className="enter mt-3 rounded border border-line bg-ink/60 px-3 py-2 text-[12px]" data-testid="explanation">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-faint">AI explains</span>
        {ev.mock && <span className="chip border-hold/50 text-hold">MOCK</span>}
        <span className="chip">risk {ev.risk_score}/100</span>
        <span className="text-faint">· arrived {ms(ev.ms)} after the verdict</span>
      </div>
      <p className="whitespace-pre-wrap text-fg" title={ev.intent_summary}>{humanize(ev.intent_summary, labels)}</p>
      <p className="mt-1 whitespace-pre-wrap text-dim">{ev.explanation}</p>
    </div>
  );
}

export function RequestCard({ index, item, labels, explanation, held }:
  { index: number; item: any; labels: Record<string, string>; explanation: any; held: any }) {
  if (item.action) {
    return (
      <article className="panel enter border-human/40 px-4 py-3" data-testid="action-card">
        <p className="text-[12px] text-faint">#{index} human action</p>
        <p className="mt-1 text-human">{item.by}: {item.label}</p>
        <p className="text-[12px] text-dim">{item.action}{item.target != null ? ` ${item.target}` : ""} → {item.done ? "done, logged" : "nothing to reset"}</p>
      </article>
    );
  }
  const c = item.check;
  const sim = c.simulation;
  const signed = item.signer?.signed;
  const reason = String(item.signer?.reason ?? "").replace(/^refused:\s*/i, "");
  const blocking = (c.findings ?? []).filter((f: any) => f.action !== "ALLOW");
  return (
    <article className={`panel enter px-4 py-3 ${c.verdict === "BLOCK" ? "border-deny/40" : ""}`} data-testid="request-card"
      data-verdict={c.verdict}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-fg"><span className="text-faint">#{index} </span>{item.label}</p>
        <div className="flex items-center gap-2">
          <span className="chip">{c.path ?? "-"} path</span>
          <span className="text-[12px] text-faint">{ms(c.timings_ms?.total)}</span>
          <VerdictPill v={c.verdict} big />
        </div>
      </div>
      {c.path_reasons?.[0] && <p className="mt-1 text-[12px] text-faint">why this path: {humanize(c.path_reasons.join("; "), labels)}</p>}

      {blocking.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {blocking.map((f: any, i: number) => (
            <li key={i} className="text-[13px]">
              <VerdictPill v={f.action} /> <span className="text-faint">{f.rule_id}</span>{" "}
              <span className="text-fg">{f.title}</span>
              {f.detail && <span className="block pl-1 text-[12px] text-dim hex" title={f.detail}>{humanize(f.detail, labels)}</span>}
            </li>
          ))}
        </ul>
      )}

      {sim?.ok && <DiffView diff={sim.state_diff} labels={labels} />}
      {sim?.ok && <ProbeView probe={sim.probe} labels={labels} />}
      {sim && !sim.ok && <p className="mt-3 text-[12px] text-deny">sandbox failed → R0 BLOCK: {sim.error}</p>}

      {(c.breaker ?? []).map((b: string, i: number) => (
        <p key={i} className="mt-2 text-[12px] text-hold">breaker: {humanize(b, labels)}</p>
      ))}
      {held && (
        <p className="mt-2 text-[12px] text-hold" data-testid="delay-outcome">
          after the hold ({held.skipped} min skipped by the demo): <b>{String(held.status).toUpperCase()}</b>
          {held.signed ? " · signed" : " · not signed"} — {humanize(held.reason, labels)}
        </p>
      )}

      <p data-testid="signer-result"
        className={`mt-3 text-[20px] font-bold leading-tight sm:text-[22px] ${signed ? "text-signal" : "text-deny"}`}>
        {signed ? "✓ Signed" : `✕ Refused: ${reason}`}
      </p>
      <Explanation ev={explanation} pending={!!c.explanation_pending} labels={labels} />
    </article>
  );
}
