import { eth, short } from "../lib/api";
import { VERDICT_WORD, REPLAY_TITLE } from "../lib/copy";
import type { Run } from "../lib/store";
import { Empty, verdictTone } from "./ui";

const DOT: Record<string, string> = {
  ok: "bg-ok", held: "bg-held", review: "bg-review", bad: "bg-bad", neutral: "bg-faint", brand: "bg-brand",
};

function describe(e: any): { text: string; tone: string } | null {
  const d = e.data ?? {};
  switch (e.type) {
    case "run_started": return { text: `Replay started: ${REPLAY_TITLE[d.scenario] ?? d.scenario}`, tone: "brand" };
    case "received": return { text: `Request received from ${short(d.from)} (${d.authorizations ? `${d.authorizations} delegation authorizations` : eth(d.value ?? "0")})`, tone: "neutral" };
    case "sandbox_started": return { text: "Sandbox check started on a fresh, isolated test chain", tone: "neutral" };
    case "verdict": return { text: `Verdict: ${VERDICT_WORD[d.verdict as keyof typeof VERDICT_WORD] ?? d.verdict}`, tone: verdictTone(d.verdict) };
    case "signer": return { text: d.signed ? "Signer: signed" : "Signer: signature refused", tone: d.signed ? "ok" : "bad" };
    case "explanation": return d.available ? { text: "AI analysis ready", tone: "neutral" } : null;
    case "human_action": return { text: `Operator ${d.by}: ${String(d.action).replace(/_/g, " ")}`, tone: "review" };
    case "human_decision": return { text: `Reviewer ${d.decision === "approve" ? "approved" : "rejected"} a held request`, tone: "review" };
    default: return null;
  }
}

export function ActivityFeed({ runs, limit = 12 }: { runs: Run[]; limit?: number }) {
  const events = runs.flatMap((r) => r.events).sort((a, b) => b.seq - a.seq);
  const lines = events.map((e) => ({ e, d: describe(e) })).filter((x) => x.d).slice(0, limit);
  if (!lines.length) {
    return <Empty title="No activity yet">Run an incident replay to see each check as it happens.</Empty>;
  }
  return (
    <ol className="divide-y divide-border" data-testid="activity" aria-live="polite">
      {lines.map(({ e, d }) => (
        <li key={`${e.run_id}.${e.seq}`} className="enter flex items-start gap-3 py-2.5">
          <span className={`mt-1.5 size-2 shrink-0 rounded-full ${DOT[d!.tone] ?? "bg-faint"}`} aria-hidden />
          <span className="min-w-0 flex-1 text-[13px] text-ink">{d!.text}</span>
          <time className="shrink-0 text-[12px] text-faint tabular-nums">
            {new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </time>
        </li>
      ))}
    </ol>
  );
}
