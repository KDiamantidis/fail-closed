import { Addr, Card, Pill } from "./ui";

const num = (v: string | number) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 4 }) : String(v);
};

function List({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="text-[13px] text-muted">Nothing.</p>;
  const chains = [...new Set(rows.map((r) => r.chain_id))];
  return (
    <ul className="space-y-1 text-[13px]">
      {chains.map((c) => (
        <li key={c} className="flex gap-2">
          <span className="w-20 shrink-0 text-muted">Chain {c}</span>
          <span className="min-w-0 text-ink">{rows.filter((r) => r.chain_id === c).map((r) => `${num(r.units)} ${r.asset_name}`).join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

export function BlastRadius({ data, labels }: { data: any[]; labels: Record<string, string> }) {
  if (!data?.length) return null;
  return (
    <div className="space-y-4">
      {data.map((br) => {
        const leftOn = [...new Set((br.allowed ?? []).map((r: any) => r.chain_id))];
        const stoppedOn = br.chains_reached.filter((c: number) => !leftOn.includes(c));
        const reached = leftOn.length;
        return (
          <Card key={br.address} testid="blast-radius"
            title={<h2 className="card-title">Blast radius</h2>}
            action={<Pill tone="bad" dot>Address frozen ({br.frozen_by})</Pill>}>
            <p className="text-[13px] text-muted">
              Destination <Addr a={br.address} labels={labels} />. What this address received before the freeze, and what never left.
            </p>
            <div className="mt-4 grid gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-border p-4">
                <p className="label">Value released before the freeze</p>
                <p className="mt-1 text-[20px] font-semibold text-ink" data-testid="br-released">
                  {num(br.allowed_totals.native_units)} ETH
                  <span className="text-[14px] font-medium text-muted"> + {num(br.allowed_totals.token_units)} token units</span>
                </p>
                <div className="mt-3"><List rows={br.allowed} /></div>
              </div>
              <div className="rounded-lg border border-border p-4">
                <p className="label">Value held and never released</p>
                <p className="mt-1 text-[20px] font-semibold text-ink" data-testid="br-held">
                  {num(br.held_totals.native_units)} ETH
                  <span className="text-[14px] font-medium text-muted"> + {num(br.held_totals.token_units)} token units</span>
                </p>
                <div className="mt-3"><List rows={br.held} /></div>
              </div>
              <div className="rounded-lg border border-border p-4">
                <p className="label">Chains reached</p>
                <p className="mt-1 text-[20px] font-semibold text-ink" data-testid="br-chains">
                  {reached} of {br.real_attack_chains}
                  <span className="text-[14px] font-medium text-muted"> in a real attack of this kind</span>
                </p>
                <div className="mt-3 flex gap-1" aria-hidden>
                  {Array.from({ length: br.real_attack_chains }, (_, i) => (
                    <i key={i} className={`h-2 flex-1 rounded-full ${i < reached ? "bg-bad/70" : "bg-border"}`} />
                  ))}
                </div>
                <p className="mt-2 text-[12px] text-muted">
                  Value left on chain {leftOn.join(", ") || "none"}.
                  {stoppedOn.length > 0 && ` Tried and stopped on chain ${stoppedOn.join(", ")}.`}
                </p>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
