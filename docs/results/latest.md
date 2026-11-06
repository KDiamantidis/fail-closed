# Load test 20261106-111314

**500 normal + 1 attack**, concurrency 8. Machine: Windows-11-10.0.26300-SP0, 16 CPUs; Docker 29.8.0 (Docker Desktop, 16 CPUs, 7.4 GiB). **Mock LLM**: 800 ms per call, escalates 5% of plain transfers.

| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms | Throughput | Total | Sandboxes started | New destinations (simulated) | Large amounts (AI asked) | Escalated | AI check unavailable | LLM calls (failed) | 429s | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fresh/escalation-on | 1/1 | 0 | 2 / 7 / 1049 (n=445) | 2408 / 2822 / 4007 (n=56) | 26.9 req/s | 18.6 s | 56 | 54 (54) | 12 (12) | 1 | 0 | 68 (0) | 0 | n/a |
| fresh/escalation-off | 1/1 | 0 | 7 / 12 / 14 (n=500) | 1732 / 1732 / 1732 (n=1) | 237.8 req/s | 2.11 s | 1 | 54 (0) | 12 (0) | 0 | 0 | 1 (0) | 0 | n/a |
| warm/escalation-on | 1/1 | 0 | 3 / 12 / 919 (n=445) | 1251 / 1524 / 2233 (n=56) | 48.9 req/s | 10.25 s | 4 | 54 (54) | 12 (12) | 1 | 0 | 68 (0) | 0 | n/a |
| warm/escalation-off | 1/1 | 0 | 6 / 10 / 12 (n=500) | 1024 / 1024 / 1024 (n=1) | 376.0 req/s | 1.33 s | 4 | 54 (0) | 12 (0) | 0 | 0 | 1 (0) | 0 | n/a |
