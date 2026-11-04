import { useEffect, useState } from "react";
import { eth, getJSON, humanize, postJSON } from "../lib/api";
import { Addr, Section } from "./ui";

export function Pending({ runId, initial, labels }: { runId: string | null; initial: any[]; labels: Record<string, string> }) {
  const [items, setItems] = useState<any[]>(initial);
  const [decided, setDecided] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setItems(initial); setDecided({}); setError(null); }, [runId, initial]);

  async function decide(id: string, decision: "approve" | "reject") {
    if (!runId) return;
    setError(null);
    try {
      const r = await postJSON(`/demo/${runId}/pending/${encodeURIComponent(id)}/${decision}`);
      setDecided((d) => ({ ...d, [id]: r }));
      setItems(await getJSON(`/demo/${runId}/pending`));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  const done = Object.entries(decided);
  if (!runId || (!items.length && !done.length)) return null;
  return (
    <Section id="pending" cmd="pending --mine" title="a human decides (only in your own demo run)">
      <div className="space-y-2" data-testid="pending">
        {items.map((p) => (
          <div key={p.id} className="panel flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0 text-[13px]">
              <p><span className="text-human">NEEDS HUMAN</span> <span className="text-faint">· {p.rule_ids.join(", ")}</span></p>
              <p className="text-dim">
                {eth(p.request.value)} from <Addr a={p.request.from} labels={labels} /> to <Addr a={p.request.to} labels={labels} />
              </p>
              <p className="text-[12px] text-faint hex">{humanize(p.reasons[0], labels)}</p>
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn btn-signal" onClick={() => decide(p.id, "approve")} data-testid="approve">Approve</button>
              <button type="button" className="btn btn-deny" onClick={() => decide(p.id, "reject")} data-testid="reject">Reject</button>
            </div>
          </div>
        ))}
        {done.map(([id, r]) => (
          <p key={id} className={`panel px-4 py-3 text-[13px] ${r.signed ? "text-signal" : "text-deny"}`} data-testid="decision">
            {r.status === "approved" && "Approved: the rules were checked again, nothing new → "}
            {r.status === "blocked" && "Not approved: "}
            {r.status === "rejected" && "Rejected. "}
            <b>{r.signed ? "Signed" : "Not signed"}</b>
            {r.reason ? <span className="block text-[12px] text-dim">{humanize(r.reason, labels)}</span> : null}
          </p>
        ))}
        {error && <p className="text-deny" role="alert">{error}</p>}
      </div>
    </Section>
  );
}
