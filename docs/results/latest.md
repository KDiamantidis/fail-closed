# Load test 20261103-233619

**500 normal + 1 attack**, concurrency 8. Machine: Windows-11-10.0.26300-SP0, 16 CPUs; Docker 29.8.0 (Docker Desktop, 16 CPUs, 7.4 GiB). **Mock LLM**: 800 ms per call, escalates 5% of plain transfers.

| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms | Throughput | Total | Sandboxes started |
|---|---|---|---|---|---|---|---|
| fresh/escalation-on | 1/1 | 0 | 804 / 810 / 822 (n=475) | 3421 / 3885 / 4210 (n=26) | 8.3 req/s | 60.68 s | 26 |
| fresh/escalation-off | 1/1 | 0 | 14 / 19 / 24 (n=500) | 4120 / 4120 / 4120 (n=1) | 110.7 req/s | 4.52 s | 1 |
| warm/escalation-on | 1/1 | 0 | 804 / 808 / 816 (n=475) | 2514 / 2727 / 3159 (n=26) | 8.8 req/s | 56.8 s | 4 |
| warm/escalation-off | 1/1 | 0 | 16 / 23 / 26 (n=500) | 3411 / 3411 / 3411 (n=1) | 129.5 req/s | 3.87 s | 4 |
