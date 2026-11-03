# Fail Closed: Signing Guard

An AI agent that checks an EVM transaction **before you sign it**. It runs the
transaction on a throwaway Anvil chain inside Docker, looks at what really
changes, explains the risk in plain words, and returns **ALLOW** or **BLOCK**.

Built for the Vultr Agent Rush hackathon (Challenge 1, "Blast Radius Zero").

## Core rule: fail closed

- Simulation fails, times out, or is unclear: **BLOCK**.
- Deterministic checks (e.g. unlimited approval) can BLOCK on their own.
- The LLM can add a BLOCK and explains the result, but it can **never** turn a
  deterministic BLOCK into ALLOW.

## Layout

| Path | What |
|---|---|
| `app/` | FastAPI app, config, verdict models |
| `sandbox/` | Sandbox image (Foundry) and the Python runner |
| `policies/` | Deterministic rules and the fail-closed combiner |
| `agent/` | Vultr Serverless Inference client (with mock mode) |
| `tests/` | pytest suite |
| `demo/` | Sample transactions |

How to run: coming in a later commit.
