# Load test 20261106-111340

**49 normal + 1 attack**, concurrency 8. Machine: Windows-11-10.0.26300-SP0, 16 CPUs; Docker 29.8.0 (Docker Desktop, 16 CPUs, 7.4 GiB). **Real LLM**: Vultr `glm-5.3-flash`, reasoning_effort minimal.

| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms | Throughput | Total | Sandboxes started | New destinations (simulated) | Large amounts (AI asked) | Escalated | AI check unavailable | LLM calls (failed) | 429s | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| warm/escalation-on | 1/1 | 0 | 7 / 14 / 20 (n=41) | 1308 / 2474 / 2480 (n=9) | 15.9 req/s | 3.15 s | 4 | 5 (5) | 3 (3) | 0 | 3 (timeout 2 s 2, busy, 3 AI calls already running 1) | 11 (0) | 0 | $0.0149 |
