import { Section } from "./ui";

export function Why() {
  return (
    <Section id="why" cmd="cat incidents.log" title="why this exists">
      <div className="panel divide-y divide-line">
        <p className="px-4 py-3">
          <span className="text-deny">29 Oct · Binance</span>
          <span className="text-dim"> — one transaction carried </span>
          50 EIP-7702 authorizations<span className="text-dim"> and delegated a hot wallet to an attacker's sweeper.</span>
        </p>
        <p className="px-4 py-3">
          <span className="text-deny">2 Nov · Mercado Bitcoin</span>
          <span className="text-dim"> — plain transfers: </span>the same address on 10 chains
          <span className="text-dim">, about 185 tokens sent one by one.</span>
        </p>
      </div>
      <p className="mt-2 text-[12px] text-faint">
        Both signers signed what they were asked to sign. The scenarios below replay the same patterns on a local test chain.
      </p>
    </Section>
  );
}
