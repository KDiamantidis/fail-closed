# Load test 20261105-170214

**500 normal + 1 attack**, concurrency 8. Machine: Windows-11-10.0.26300-SP0, 16 CPUs; Docker 29.8.0 (Docker Desktop, 16 CPUs, 7.4 GiB). **Real LLM**: Vultr `glm-5.3-flash`, reasoning_effort minimal.

| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms | Throughput | Total | Sandboxes started | New destinations | AI asked | Escalated | AI check unavailable | LLM calls (failed) | 429s | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| warm/escalation-on | 1/1 | 0 | 4 / 13 / 28 (n=450) | 3011 / 4494 / 5291 (n=51) | 22.8 req/s | 21.93 s | 4 | 50 | 50 | 0 | 50 (timeout 2 s 15, busy, 3 AI checks already running 35) | 73 (7) | 0 | $0.0916 |
| warm/escalation-off | 1/1 | 0 | 16 / 26 / 32 (n=500) | 2551 / 2551 / 2551 (n=1) | 168.1 req/s | 2.98 s | 4 | 50 | 0 | 0 | 0 | 1 (0) | 0 | $0.0011 |
