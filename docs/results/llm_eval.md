# Model comparison (2026-11-04, laptop, Vultr Serverless Inference)

20 labelled requests (12 normal, 8 attack-shaped; the last 4 held out, written after the prompt was tuned) on the fast-path escalation prompt and 4 explanation prompts with a sandbox result, one call each, sequential, no retries.

| Model | reasoning | p50 | p95 | under 2 s | valid JSON | normal not escalated | attack escalated | injection flagged | cost |
|---|---|---|---|---|---|---|---|---|---|
| `glm-5.3-flash` | minimal | 933 ms | 1148 ms | 24/24 | 24/24 | 12/12 | 7/8 | yes | $0.0030 |
| `glm-5.3` | minimal | 4243 ms | 30012 ms | 5/24 | 22/24 | 12/12 | 7/7 | yes | $0.0214 |
| `qwen3.8-flash-next` | minimal | 2026 ms | 3025 ms | 12/24 | 24/24 | 12/12 | 8/8 | yes | $0.0054 |
| `glm-5.3-flash` | default | 2265 ms | 4037 ms | 10/24 | 19/24 | 12/12 | 4/4 | no | $0.0091 |
