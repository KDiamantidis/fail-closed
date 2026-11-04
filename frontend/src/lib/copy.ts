// Plain business English for everything the Guard says. One place, so the UI speaks one way.
import type { Verdict } from "./api";

export const VERDICT_WORD: Record<Verdict | "BUSY", string> = {
  ALLOW: "Approved",
  DELAY: "Held",
  NEEDS_HUMAN: "Needs review",
  BLOCK: "Blocked",
  BUSY: "Blocked",
};

export const PATH_WORD: Record<string, string> = {
  fast: "Instant",
  slow: "Sandbox check",
  early: "Stopped early",
};

/** What a rule found, as the end of a sentence ("Blocked: <this>"). */
export function ruleReason(ruleId: string, action: string): string {
  switch (ruleId) {
    case "R0": return "the sandbox check could not be completed";
    case "R1": return "the wallet would hand control to an unknown contract";
    case "R2": return "the request carries several delegation authorizations at once";
    case "R3": return action === "BLOCK"
      ? "the wallet would lose most of its balance to one address"
      : "the withdrawal is a large share of the wallet balance";
    case "R4": return "the amount is above the treasury limit";
    case "R5": return action === "BLOCK"
      ? "an unknown destination and an amount above the limit"
      : "the destination is not on the treasury allowlist";
    case "R6": return "the wallet has sent more than its hourly limit";
    case "R7": return action === "BLOCK"
      ? "the same address is being paid from 5 chains"
      : "the same address is being paid from several chains";
    case "R8": return "many different assets are going to one address";
    case "C1": return "a customer wallet was asked to sign something other than a plain transfer";
    case "F1": return "the destination address is frozen";
    case "F2": return "the wallet is paused";
    case "F3": return "the chain is in safe mode";
    case "F4": return "all approvals are stopped (global stop)";
    default: return "a policy rule matched";
  }
}

// Which finding explains the verdict best, most serious first.
const PRIORITY = ["R1", "R3:BLOCK", "R2", "F4", "F1", "F2", "R7:BLOCK", "R8", "R6", "R5:BLOCK", "R0",
  "R7", "R5", "R4", "R3", "F3", "C1"];

export function headline(check: any): string {
  const v = check.verdict as Verdict;
  const findings = (check.findings ?? []).filter((f: any) => f.action !== "ALLOW");
  if (v === "ALLOW") {
    return check.path === "fast"
      ? "Approved: a plain transfer within every limit"
      : "Approved: the sandbox check found nothing unusual";
  }
  const ranked = [...findings].sort((a: any, b: any) => rank(a) - rank(b));
  const top = ranked.find((f: any) => f.action === v) ?? ranked[0];
  return `${VERDICT_WORD[v]}: ${top ? ruleReason(top.rule_id, top.action) : "a policy rule matched"}`;
}

export function rank(f: any): number {
  const i = PRIORITY.indexOf(`${f.rule_id}:${f.action}`);
  const j = PRIORITY.indexOf(f.rule_id);
  return i >= 0 ? i : j >= 0 ? j : 99;
}

/** Why the Guard chose this kind of check, in plain words. */
export function pathWords(path: string): string {
  if (path === "fast") return "A plain transfer within the limits, so no sandbox check was needed.";
  if (path === "early") return "Stopped before the sandbox: the address is frozen or the wallet is paused.";
  return "This is not a plain transfer, so it was first run on an isolated copy of the chain to see what it really does.";
}

/** Incident replays: plain names, featured first. */
export const REPLAYS: { name: string; title: string; blurb: string; featured: boolean }[] = [
  { name: "d_binance_7702", title: "Binance-style EIP-7702 delegation (29 Oct)", featured: true,
    blurb: "A hot wallet is asked to sign 5 delegation authorizations to a sweeper contract." },
  { name: "e_mercado_multichain", title: "Mercado-style multi-chain drain (2 Nov)", featured: true,
    blurb: "One address paid on 5 chains, token by token, after a small test transfer." },
  { name: "f_honest_large_withdrawal", title: "Honest large withdrawal", featured: true,
    blurb: "A real customer takes out 40% of a hot wallet. A person decides; nobody else is stopped." },
  { name: "g_coordinated_breaker", title: "Coordinated attack (breaker levels 1-4)", featured: true,
    blurb: "Several wallets and chains attacked at once, then an operator resets the breaker." },
  { name: "a_customer_small_withdrawal", title: "Small customer withdrawal", featured: false,
    blurb: "An everyday payout. Approved instantly." },
  { name: "b_treasury_allowlisted_above_limit", title: "Treasury payment above the limit", featured: false,
    blurb: "A known destination, but more than the treasury limit." },
  { name: "c_treasury_unknown_above_limit", title: "Treasury payment to an unknown address", featured: false,
    blurb: "Not on the allowlist and above the limit." },
];

export const REPLAY_TITLE: Record<string, string> = Object.fromEntries(REPLAYS.map((r) => [r.name, r.title]));
