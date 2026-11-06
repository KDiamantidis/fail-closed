// The risk colour scale. It follows the circuit breaker level: calm when normal, more red as
// danger rises. Never colour alone: every use shows the level name, an icon and a sentence.
// Text/background pairs are WCAG AA (all >= 7:1); dots and icons >= 3:1 on white.

export type RiskLevel = 0 | 1 | 2 | 3 | 4;

export interface RiskMeta {
  name: string;
  strip: string;   // thin strip: background, border, text
  card: string;    // KPI card tint
  text: string;    // coloured text on a light background
  dot: string;     // the small dot (non-text contrast >= 3:1)
  icon: "check" | "alert" | "stop";
}

export const RISK: Record<RiskLevel, RiskMeta> = {
  0: { name: "Normal", strip: "bg-surface border-border text-body", card: "bg-surface border-border",
       text: "text-ink", dot: "bg-risk0-dot", icon: "check" },
  1: { name: "Elevated", strip: "bg-risk1-bg border-risk1-line text-risk1", card: "bg-risk1-bg border-risk1-line",
       text: "text-risk1", dot: "bg-risk1-dot", icon: "alert" },
  2: { name: "High", strip: "bg-risk2-bg border-risk2-line text-risk2", card: "bg-risk2-bg border-risk2-line",
       text: "text-risk2", dot: "bg-risk2-dot", icon: "alert" },
  3: { name: "Critical", strip: "bg-risk3-bg border-risk3-line text-risk3", card: "bg-risk3-bg border-risk3-line",
       text: "text-risk3", dot: "bg-risk3-dot", icon: "alert" },
  4: { name: "Global stop", strip: "bg-risk4-bg border-risk4-line text-white", card: "bg-risk4-bg border-risk4-line",
       text: "text-white", dot: "bg-risk4-bg", icon: "stop" },
};

export function levelOf(state: any): RiskLevel {
  if (!state) return 0;
  if (state.global_stop) return 4;
  if (state.safe_mode_chains?.length) return 3;
  if (state.paused_wallets?.length) return 2;
  if (state.frozen_destinations?.length) return 1;
  return 0;
}

const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;

/** One plain sentence for the level, e.g. "Critical: 2 wallets paused, chain 1 in safe mode". */
export function riskSentence(state: any, level: RiskLevel = levelOf(state)): string {
  if (level === 4) return "Global stop: no withdrawals are being signed";
  if (level === 0) return "Normal: no limits in force, every request is checked as usual";
  const parts: string[] = [];
  const paused = state?.paused_wallets?.length ?? 0;
  const chains: number[] = (state?.safe_mode_chains ?? []).map((c: any) => c.chain_id);
  const frozen = state?.frozen_destinations?.length ?? 0;
  if (paused) parts.push(n(paused, "wallet paused", "wallets paused"));
  if (chains.length) parts.push(`${chains.length === 1 ? "chain" : "chains"} ${chains.join(" and ")} in safe mode`);
  if (frozen) parts.push(n(frozen, "address frozen", "addresses frozen"));
  return `${RISK[level].name}: ${parts.join(", ")}`;
}
