// Talking to the Guard API. Everything that comes back is untrusted data and is rendered as
// plain text only (React escapes it; we never use dangerouslySetInnerHTML).

export type Verdict = "ALLOW" | "DELAY" | "NEEDS_HUMAN" | "BLOCK";

export interface GuardEvent {
  seq: number;
  ts: number;
  type: string;
  run_id: string;
  check_id: string;
  data: any;
}

export async function getJSON<T = any>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export async function postJSON<T = any>(path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `${path}: HTTP ${r.status}`);
  return data as T;
}

export function newRunId(): string {
  const a = new Uint8Array(6);
  crypto.getRandomValues(a);
  return "ui" + Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

const EVENT_TYPES = ["run_started", "received", "decoded", "request_rules", "path", "sandbox_started",
  "sandbox_done", "findings", "verdict", "approval", "signer", "explanation", "human_action",
  "human_decision", "hold_blocked", "run_finished", "breaker_state", "loadtest_started",
  "loadtest_request", "loadtest_finished"];

/** How live updates arrive: "live" (Server-Sent Events), "polling" (GET /events every
 * POLL_MS), "delayed" (polling failed or was rate-limited; retrying with back-off). */
export type LiveState = "live" | "polling" | "delayed";
export const POLL_MS = 2000;

/** Subscribe to one run's events. Server-Sent Events first; if the stream errors or brings no
 * event within a few seconds (e.g. a tunnel that does not pass SSE), or `forcePoll` is set, it
 * polls GET /events instead. Events are delivered once each (by seq). Resolves when ready. */
export function subscribeRun(runId: string, onEvent: (e: GuardEvent) => void,
                             opts: { forcePoll?: boolean; onState?: (s: LiveState) => void } = {}): Promise<() => void> {
  return new Promise((resolve) => {
    let closed = false, gotEvent = false, failures = 0, lastSeq = 0;
    let es: EventSource | null = null;
    let timer: number | undefined;
    const seen = new Set<number>();
    const deliver = (e: GuardEvent) => {
      if (closed || seen.has(e.seq)) return;
      seen.add(e.seq);
      lastSeq = Math.max(lastSeq, e.seq);
      gotEvent = true;
      onEvent(e);
    };
    const poll = async () => {
      if (closed) return;
      try {
        const evs = await getJSON<GuardEvent[]>(`/events?after=${lastSeq}&run_id=${encodeURIComponent(runId)}`);
        failures = 0;
        opts.onState?.("polling");
        for (const e of evs) deliver(e);
      } catch {
        failures += 1;
        opts.onState?.(failures >= 2 ? "delayed" : "polling");
      }
      if (!closed) timer = window.setTimeout(poll, failures ? Math.min(15000, POLL_MS * 2 ** failures) : POLL_MS);
    };
    const startPolling = () => {
      if (es) { es.close(); es = null; }
      if (timer === undefined && !closed) { opts.onState?.("polling"); timer = window.setTimeout(poll, 0); }
    };
    const close = () => {
      closed = true;
      es?.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
    if (opts.forcePoll || typeof EventSource === "undefined") {
      startPolling();
      resolve(close);
      return;
    }
    es = new EventSource(`/stream?run_id=${encodeURIComponent(runId)}`);
    for (const t of EVENT_TYPES) {
      es.addEventListener(t, (m) => {
        try { deliver(JSON.parse((m as MessageEvent).data)); } catch { /* ignore a malformed event */ }
      });
    }
    es.onerror = () => startPolling();  // a broken or blocked stream: poll instead, nothing is lost (seq)
    let done = false;
    const ready = () => { if (!done) { done = true; resolve(close); } };
    es.addEventListener("open", () => { opts.onState?.("live"); ready(); });
    window.setTimeout(ready, 1500);  // never block the run on the stream
    // the run publishes "run_started" at once: no event at all after 4 s means the stream is not getting through
    window.setTimeout(() => { if (!gotEvent && !closed) startPolling(); }, 4000);
  });
}

/** POST that answers with Server-Sent Events (EventSource can only GET). */
export async function postStream(path: string, onEvent: (type: string, data: any) => void,
                                 headers: Record<string, string> = {}): Promise<void> {
  const r = await fetch(path, { method: "POST", headers: { Accept: "text/event-stream", ...headers } });
  if (!r.ok || !r.body) {
    const data = await r.json().catch(() => ({}));
    throw new Error(typeof data?.detail === "string" ? data.detail : `${path}: HTTP ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let type = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7).trim();
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (data.length) {
        try { onEvent(type, JSON.parse(data.join("\n"))); } catch { /* ignore */ }
      }
    }
  }
}

// ---- formatting (all plain text) ----------------------------------------------------------

export function short(addr: string | null | undefined): string {
  if (!addr) return "";
  return /^0x[0-9a-fA-F]{40}$/.test(addr) ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}

/** Plain-text clean-up of server text: long addresses become 0x1234…abcd (label). Text in, text out. */
export function humanize(text: string, labels: Record<string, string> = {}): string {
  return String(text ?? "").replace(/0x[0-9a-fA-F]{40}(?![0-9a-fA-F])/g, (a) => {
    const label = labels[a.toLowerCase()];
    return `${short(a)}${label ? ` (${label})` : ""}`;
  });
}

export function eth(wei: string | number | bigint): string {
  let v: bigint;
  try { v = BigInt(wei); } catch { return String(wei); }
  const neg = v < 0n;
  if (neg) v = -v;
  const whole = v / 10n ** 18n;
  const frac = (v % 10n ** 18n).toString().padStart(18, "0").slice(0, 4).replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole.toLocaleString("en-US")}${frac ? "." + frac : ""} ETH`;
}

export function ms(v: number | null | undefined): string {
  if (v == null) return "-";
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
}

export function hexToInt(v: unknown): bigint {
  if (typeof v === "number") return BigInt(v);
  if (typeof v === "string" && v.startsWith("0x")) return BigInt(v);
  return 0n;
}
