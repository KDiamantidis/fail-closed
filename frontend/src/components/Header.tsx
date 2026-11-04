export interface Status {
  sandbox_mode: string;
  llm: "REAL" | "MOCK" | "OFF";
  model: string;
  breaker_level: number;
  redteam_public: boolean;
  delay_minutes: number;
}

const LEVEL_TEXT = ["normal", "address frozen", "wallet paused", "chain safe mode", "GLOBAL STOP"];

export function Header({ status }: { status: Status | null }) {
  const lvl = status?.breaker_level ?? 0;
  return (
    <header>
      {/* the terminal window chrome from the covers */}
      <div className="flex items-center justify-between gap-3 border-b border-line bg-chrome px-4 py-2.5 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="flex gap-1.5" aria-hidden>
            <i className="size-2.5 rounded-full bg-[#ff5f57]" />
            <i className="size-2.5 rounded-full bg-[#febc2e]" />
            <i className="size-2.5 rounded-full bg-[#28c840]" />
          </span>
          <span className="hidden text-[12px] text-faint sm:inline">guard@fail-closed:~</span>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5" aria-label="System status">
          <span className="chip" data-testid="chip-sandbox">sandbox <b className="text-fg">{status?.sandbox_mode ?? "…"}</b></span>
          <span className="chip" data-testid="chip-llm" title={status ? `model ${status.model}` : ""}>
            LLM <b className={status?.llm === "REAL" ? "text-signal" : "text-hold"}>{status?.llm ?? "…"}</b>
          </span>
          <span className="chip" data-testid="chip-breaker">
            breaker <b className={lvl >= 3 ? "text-deny" : lvl > 0 ? "text-hold" : "text-signal"}>
              L{lvl} {LEVEL_TEXT[lvl]}
            </b>
          </span>
        </div>
      </div>

      <div className="mx-auto max-w-[1120px] px-4 pt-10 pb-6 sm:px-6 sm:pt-14">
        <p className="text-[13px] text-signal">$ run --policy deny-by-default</p>
        <h1 className="mt-3 text-[44px] leading-none font-bold tracking-[-0.04em] text-signal sm:text-[76px]">
          FAIL CLOSED<span className="cursor ml-2 inline-block h-[0.78em] w-[0.42em] translate-y-[0.06em] bg-signal" aria-hidden />
        </h1>
        <p className="mt-4 text-[18px] text-fg sm:text-[22px]">No approval, no signature.</p>
        <p className="mt-3 max-w-[680px] text-dim">
          An independent co-signer. Before a wallet signs anything, the Guard runs the request in a
          throw-away sandbox, reads what really changes, and lets fixed rules decide. The AI only explains.
          If anything is unsure or broken, the answer is <span className="text-deny">BLOCK</span>.
        </p>
      </div>
    </header>
  );
}
