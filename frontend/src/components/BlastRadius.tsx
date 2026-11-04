import { Addr, Section } from "./ui";

function Rows({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="text-faint">nothing</p>;
  const chains = [...new Set(rows.map((r) => r.chain_id))];
  return (
    <ul className="space-y-0.5">
      {chains.map((c) => (
        <li key={c} className="hex">
          <span className="text-faint">chain {c}: </span>
          {rows.filter((r) => r.chain_id === c).map((r) => `${r.units} ${r.asset_name}`).join(", ")}
        </li>
      ))}
    </ul>
  );
}

export function BlastRadius({ data, labels }: { data: any[]; labels: Record<string, string> }) {
  if (!data?.length) return null;
  return (
    <Section id="blast-radius" cmd="blast-radius" title="what got through before the freeze">
      <div className="space-y-3">
        {data.map((br) => {
          const reached = br.chains_reached.length;
          return (
            <div key={br.address} className="panel px-4 py-3" data-testid="blast-radius">
              <p>
                <Addr a={br.address} labels={labels} />
                <span className="text-faint"> · frozen by </span><span className="text-deny">{br.frozen_by}</span>
              </p>
              <div className="mt-3 grid gap-4 sm:grid-cols-3">
                <div>
                  <p className="text-[12px] text-signal">ALLOWED before the freeze</p>
                  <p className="text-[12px] text-faint">would have been signed</p>
                  <div className="mt-1 text-[13px]"><Rows rows={br.allowed} /></div>
                  {br.allowed.length > 0 && (
                    <p className="mt-1 text-[12px] text-dim">
                      total {br.allowed_totals.native_units} ETH + {br.allowed_totals.token_units} tokens ({br.allowed_totals.token_types} types)
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-[12px] text-hold">held by DELAY, never released</p>
                  <p className="text-[12px] text-faint">the freeze is a new signal</p>
                  <div className="mt-1 text-[13px]"><Rows rows={br.held} /></div>
                </div>
                <div>
                  <p className="text-[12px] text-dim">chains reached</p>
                  <p className="mt-1 text-[28px] leading-none font-bold">
                    {reached}<span className="text-[14px] font-normal text-faint"> of the real attack's {br.real_attack_chains}</span>
                  </p>
                  <div className="mt-2 flex gap-1" aria-hidden>
                    {Array.from({ length: br.real_attack_chains }, (_, i) => (
                      <i key={i} className={`h-2 flex-1 rounded-sm ${i < reached ? "bg-deny/70" : "bg-line"}`} />
                    ))}
                  </div>
                  <p className="mt-1 text-[12px] text-faint">{br.chains_reached.join(" · ")}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}
