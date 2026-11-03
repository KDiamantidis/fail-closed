# Scenario: 7702_attack (Binance-style)

A compromised withdrawal service asks the signer to sign **5 EIP-7702 authorizations**
for the hot wallet, one per consecutive nonce (3 to 7), all delegating to `MockSweeper`.
The hot wallet's current nonce is 7, so exactly one of them matches.

The sandbox replays how the attacker uses them: a separate **attacker relayer** sends one
type-4 transaction that carries all 5 authorizations and, in the same transaction, calls
`sweep()` on the now-delegated hot wallet. Real attackers do this atomically.

| File | Who provides it |
|---|---|
| `request.json` | The untrusted withdrawal service (what the Guard is asked to approve) |
| `state.json` | The Guard side (StateProvider): balances, nonces, code. Never the request. |
| `scenario.json` | Display labels only |

Accounts are Anvil mnemonic accounts (public test keys): #1 = hot wallet, #2 = attacker.
`MockSweeper` only lets the constant attacker address call `sweep()`.

Run: `scenarios/run.ps1` (Windows) or `scenarios/run.sh` (Linux/WSL2).
