# Load test 20261103-235948

**500 normal + 1 attack**, concurrency 8. Machine: Windows-11-10.0.26300-SP0, 16 CPUs; Docker 29.8.0 (Docker Desktop, 16 CPUs, 7.4 GiB). **Mock LLM**: 800 ms per call, escalates 5% of plain transfers.

| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms | Throughput | Total | Sandboxes started |
|---|---|---|---|---|---|---|---|
| fresh/escalation-on | 1/1 | 0 | 804 / 808 / 820 (n=475) | 2638 / 3425 / 3620 (n=26) | 8.7 req/s | 57.63 s | 26 |
| fresh/escalation-off | 1/1 | 0 | 16 / 24 / 27 (n=500) | 3473 / 3473 / 3473 (n=1) | 126.6 req/s | 3.96 s | 1 |
| warm/escalation-on | 1/1 | 0 | 804 / 813 / 826 (n=475) | 1754 / 2002 / 2456 (n=26) | 9.3 req/s | 54.08 s | 4 |
| warm/escalation-off | 1/1 | 0 | 16 / 26 / 32 (n=500) | 2854 / 2854 / 2854 (n=1) | 150.7 req/s | 3.33 s | 4 |
