# Fail Closed: Signing Guard

An independent co-signer between a signing request and the signer. The signer never
signs without the Guard's signed approval, bound to the request hash.

The Guard judges the **effect** of a request, not its source: it runs the transaction
in a fresh, offline sandbox (Anvil, hardfork prague), reads the state diff, and lets
deterministic rules decide. An LLM (Vultr Serverless Inference) explains the result in
plain words. It never decides.

Built for the Vultr Agent Rush hackathon (Challenge 1, "Blast Radius Zero").
Inspired by the Binance (EIP-7702 sweeper delegation) and Mercado Bitcoin
(multi-chain, token-by-token drain) incidents.

## Verdicts

`ALLOW` < `DELAY` (hold 10-15 min, auto release) < `NEEDS_HUMAN` < `BLOCK`.
The final verdict is the strictest one any rule produced.

## Core rules

- The withdrawal service is untrusted. Sandbox starting state comes from the Guard side, never from the request.
- Sandbox trace / diff / receipt missing, empty, timed out or errored: **BLOCK** (R0). Content is checked, exit codes are not trusted.
- A rule that crashes or misses an input: **BLOCK** (R0).
- The LLM only explains (intent summary + risk score 0-100). It never decides, allows or downgrades.
  If it fails, there is no explanation and the verdict is unchanged.
- The Guard holds no wallet keys. No secrets inside the sandbox.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (`app/`), decision engine (`policies/`), tests (`tests/`) |
| `sandbox/` | Sandbox image (Foundry v1.8.5) |
| `frontend/` | Vite + React + Tailwind UI (later) |
| `scenarios/` | Attack and normal-traffic scenarios |
| `docs/` | Design notes |

## Run the tests

```sh
docker run --rm -v "$PWD/backend:/src:ro" python:3.12-slim \
  sh -c "cp -r /src /w && cd /w && pip install -q -e '.[dev]' && pytest -q"
```

## Honest limits

- The API runs on the host and needs Docker access, which is root-equivalent with normal
  Docker. Production setup: rootless Docker for the API user with cgroup v2 delegation,
  otherwise sandbox memory / CPU / pids limits are not enforced. Red-team fork-bomb and
  memory tests must prove the limits hold.
- Rule thresholds are starting values, not tuned on real traffic.
