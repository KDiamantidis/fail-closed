"""Fail Closed security suite: re-runs every attack from the security audit (F1-F18) and the
red team against a RUNNING instance, and prints ID / attack / result / evidence.

    python scripts/security_suite.py                      # http://127.0.0.1:8000, Plan B (cloudflared)
    python scripts/security_suite.py --proxy caddy        # on the VM, behind Caddy
    python scripts/security_suite.py --quick              # no red team, no simulations, no parallel withdrawals

It starts nothing. It attacks only the URL you give it (default: this machine). Visitors are
simulated the way the proxy reports them: CF-Connecting-IP (cloudflared) or X-Forwarded-For
(Caddy), with documentation addresses (198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32).
The instance must run with PUBLIC_DEMO=true. Admin checks read ADMIN_TOKEN from .env; the token
and the Vultr key are never printed (evidence is scrubbed).

Bounded load: a few hundred tiny requests, about 12 replays, one pair of simulations, 10
withdrawals, the red team; the heavy steps are skipped when free RAM is under 1.5 GB.

Results: PASS (the attack fails), FAIL (the attack works: exit code 1), KNOWN (an accepted limit,
listed in README "Honest limits"), SKIP (could not be run here: the reason is the evidence).
"""

from __future__ import annotations

import argparse
import http.client
import json
import random
import socket
import string
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

MIN_FREE_GB = 1.5
HOT = "0x1000000000000000000000000000000000000e01"  # a customer hot wallet of the demo state


def free_ram_gb() -> float | None:
    try:
        from loadtest_safety import free_ram_gb as f
        return f()
    except Exception:  # noqa: BLE001
        return None


def env_value(path: Path, name: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def rnd_run_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def rnd_address() -> str:
    return "0x" + "".join(random.choices("0123456789abcdef", k=40))


class Target:
    def __init__(self, url: str, proxy: str, admin: str, secrets: list[str]):
        u = urlparse(url)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self.proxy = proxy
        self.admin = admin
        self.secrets = [s for s in secrets if s]
        self.lowest_ram: float | None = None
        self._n = random.randrange(0, 250)  # a fresh set of visitor addresses on every run
        self._v6 = random.randrange(0x100, 0xffff)

    # visitors -------------------------------------------------------------------
    def visitor(self, ip: str) -> dict:
        return {"CF-Connecting-IP": ip} if self.proxy == "cloudflared" else {"X-Forwarded-For": ip}

    def ipv4(self, i: int) -> str:
        """Visitor i: a different address for every i below 250 (198.51.100.x below 100, then 203.0.113.x)."""
        net = "203.0.113" if i >= 100 else "198.51.100"
        return f"{net}.{(self._n + i) % 250 + 1}"

    def net64(self, i: int) -> str:
        return f"2001:db8:{self._v6:x}:{i:x}"

    def admin_headers(self, ip: str) -> dict:
        return {**self.visitor(ip), "X-Admin-Token": self.admin}

    # http -------------------------------------------------------------------------
    def req(self, method: str, path: str, headers: dict | None = None, body=None, timeout: float = 60):
        c = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            out = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, out.decode("utf-8", "replace")
        finally:
            c.close()

    def json(self, method, path, headers=None, body=None, timeout=60):
        s, h, b = self.req(method, path, headers, body, timeout)
        try:
            return s, json.loads(b)
        except ValueError:
            return s, b

    def raw(self, data: bytes, read: int = 256, timeout: float = 10) -> str:
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            s.sendall(data)
            return s.recv(read).decode("latin-1", "replace")
        finally:
            s.close()

    def scrub(self, text: str) -> str:
        for v in self.secrets:
            text = text.replace(v, "<secret>")
        return text

    def ram_ok(self) -> tuple[bool, str]:
        f = free_ram_gb()
        if f is not None:
            self.lowest_ram = f if self.lowest_ram is None else min(self.lowest_ram, f)
        if f is not None and f < MIN_FREE_GB:
            return False, f"free RAM {f:.2f} GB < {MIN_FREE_GB} GB: heavy step skipped"
        return True, ""

    def spent(self) -> float:
        s, st = self.json("GET", "/status")
        return float(((st or {}).get("llm_budget") or {}).get("spent_today_usd") or 0) if s == 200 else 0.0

    def settle_spend(self, max_s: float = 45) -> float:
        """Today's AI spend once the background explanations have finished."""
        last, same, t0 = self.spent(), 0, time.time()
        while time.time() - t0 < max_s and same < 3:
            time.sleep(2)
            now = self.spent()
            same = same + 1 if now == last else 0
            last = now
        return last


class Suite:
    def __init__(self, t: Target, quick: bool, redteam: bool):
        self.t, self.quick, self.redteam = t, quick, redteam
        self.rows: list[tuple[str, str, str, str]] = []
        self.shared: dict = {}

    def add(self, fid: str, attack: str, result: str, evidence: str) -> None:
        self.rows.append((fid, attack, result, self.t.scrub(evidence)))
        print(f"  {fid:4s} {result:5s} {attack}", flush=True)

    def run(self, fid: str, attack: str, fn) -> None:
        try:
            result, evidence = fn()
        except Exception as e:  # noqa: BLE001 - a broken check is a FAIL, never a silent pass
            result, evidence = "FAIL", f"check crashed: {type(e).__name__}: {e}"
        self.add(fid, attack, result, evidence)

    # ---- P0 --------------------------------------------------------------------------
    def p0(self):
        s, st = self.t.json("GET", "/status")
        if s != 200:
            return "FAIL", f"/status answered {s}"
        if not st.get("public_demo"):
            return "FAIL", "PUBLIC_DEMO is off: the public-mode protections are not active"
        return "PASS", f"public demo on, deploy target {st.get('deploy_target')}, live updates {st.get('live_updates')}"

    # ---- F1 parallel requests ------------------------------------------------------
    def f1(self):
        if self.quick:
            return "SKIP", "--quick (10 sandboxed withdrawals)"
        if not self.t.admin:
            return "SKIP", "no ADMIN_TOKEN in .env (/withdraw is an admin action)"
        ok, why = self.t.ram_ok()
        if not ok:
            return "SKIP", why
        chains = [1, 10, 42161, 137, 43114]
        ip = self.t.ipv4(120)

        def one(to, chain, out):
            body = {"wallet_type": "customer_hot", "chain_id": chain, "from": HOT, "to": to, "data": "0x",
                    "value": str(10**16)}
            s, j = self.t.json("POST", "/withdraw", self.t.admin_headers(ip), body, timeout=240)
            out.append(j["check"]["verdict"] if s == 200 else f"HTTP {s}")

        seq, par = [], []
        to_seq, to_par = rnd_address(), rnd_address()
        for c in chains:
            one(to_seq, c, seq)
        ts = [threading.Thread(target=one, args=(to_par, c, par)) for c in chains]
        [t.start() for t in ts]
        [t.join(300) for t in ts]
        self.t.req("POST", "/breaker/reset-all", self.t.admin_headers(ip), {})  # unfreeze the two test addresses
        cs, cp = Counter(seq), Counter(par)
        ev = f"one by one {dict(cs)}; all 5 at once {dict(cp)}"
        if any(v.startswith("HTTP") for v in seq + par):
            return "SKIP", "a withdrawal was refused (Guard busy / limits): " + ev
        if cs == cp and cs["ALLOW"] < 5:
            return "PASS", ev
        return "FAIL", ev + " (parallel requests skipped the history rules)"

    # ---- F2 other visitors' demos ---------------------------------------------------
    def f2(self):
        t = self.t
        a, b = t.visitor(t.ipv4(1)), t.visitor(t.ipv4(2))
        run = rnd_run_id()
        spent0 = t.settle_spend(20)
        s, out = t.json("POST", f"/scenarios/f_honest_large_withdrawal/run?run_id={run}", a, timeout=180)
        if s != 200 or not out.get("pending"):
            return "FAIL", f"victim replay (f) did not give a held request: HTTP {s}"
        self.shared.update(run_a=run, ip_a=t.ipv4(1), replay_f=out, spent0=spent0)
        hold = out["pending"][0]["id"]
        got = {
            "events, no run_id": t.req("GET", "/events?after=0", b)[0],
            "events run_id=main": t.req("GET", "/events?after=0&run_id=main", b)[0],
            "events of A's run": t.req("GET", f"/events?after=0&run_id={run}", b)[0],
            "stream, no run_id": t.req("GET", "/stream?limit=1", b, timeout=10)[0],
            "approve A's hold": t.req("POST", f"/demo/{run}/pending/{hold}/approve", b, {})[0],
        }
        s, reset = t.json("POST", "/demo/reset", b, {"run_ids": [run]})
        got["reset A's demo (runs reset)"] = reset.get("reset") if isinstance(reset, dict) else s
        own = t.req("GET", f"/demo/{run}/pending", a)[0]
        ev = ", ".join(f"{k} {v}" for k, v in got.items()) + f"; A's own demo {own}"
        bad = [k for k, v in got.items() if v not in (404, 0)]
        return ("PASS" if not bad and own == 200 else "FAIL"), ev

    # ---- F3 IPv6 /64 and IPv4-mapped ------------------------------------------------------
    def f3(self):
        t = self.t
        n64 = t.net64(3)
        first = None
        for i in range(1, 101):
            s = t.req("POST", "/demo/reset", t.visitor(f"{n64}::{i:x}"), {"run_ids": []})[0]
            if s == 429:
                first = i
                break
        v4 = t.ipv4(3)
        blocked = None
        for i in range(1, 101):
            if t.req("POST", "/demo/reset", t.visitor(v4), {"run_ids": []})[0] == 429:
                blocked = i
                break
        mapped = t.req("POST", "/demo/reset", t.visitor(f"::ffff:{v4}"), {"run_ids": []})[0]
        ev = (f"one /64, a new address per request: 429 at request {first}; IPv4 limited at request {blocked}, "
              f"then the same address as ::ffff:{v4} -> {mapped}")
        return ("PASS" if first and blocked and mapped == 429 else "FAIL"), ev

    # ---- F4 AI budget --------------------------------------------------------------------
    def f4(self):
        t = self.t
        s, h = t.json("GET", "/health")
        if s != 200 or h.get("llm_mode") != "live":
            return "SKIP", f"AI not live on this instance (llm_mode {h.get('llm_mode') if isinstance(h, dict) else s})"
        if "replay_f" not in self.shared:
            return "SKIP", "needs the replay of F2"
        first = t.settle_spend() - self.shared["spent0"]
        s, _ = t.json("POST", f"/scenarios/f_honest_large_withdrawal/run?run_id={rnd_run_id()}",
                      t.visitor(t.ipv4(4)), timeout=180)
        if s != 200:
            return "FAIL", f"second replay HTTP {s}"
        before = t.spent()
        again = t.settle_spend() - before
        ev = f"replay (f) by visitor A cost ${first:.4f}; the same replay by visitor B ${again:.4f} (cached)"
        return ("PASS" if again < 0.0005 else "FAIL"), ev + "; per-visitor share: unit-tested"

    # ---- F5 eviction ---------------------------------------------------------------------
    def f5(self):
        t = self.t
        victim, vrun = t.visitor(t.ipv4(5)), rnd_run_id()
        if t.req("POST", f"/scenarios/a_customer_small_withdrawal/run?run_id={vrun}", victim, timeout=180)[0] != 200:
            return "FAIL", "victim replay failed"
        n64 = t.net64(5)
        flood = []
        for i in range(6):  # more than the per-visitor cap (5), from one /64
            rid = rnd_run_id()
            s = t.req("POST", f"/scenarios/a_customer_small_withdrawal/run?run_id={rid}",
                      t.visitor(f"{n64}::{i + 1:x}"), timeout=180)[0]
            flood.append((rid, s))
        fv = t.visitor(f"{n64}::1")
        v = t.req("GET", f"/demo/{vrun}/pending", victim)[0]
        oldest = t.req("GET", f"/demo/{flood[0][0]}/pending", fv)[0]
        newest = t.req("GET", f"/demo/{flood[-1][0]}/pending", fv)[0]
        ev = (f"6 replays from one /64 ({[s for _, s in flood]}): victim's demo {v}, "
              f"flooder's oldest {oldest}, flooder's newest {newest}")
        return ("PASS" if v == 200 and oldest == 404 and newest == 200 else "FAIL"), ev

    # ---- F6 history write fails ------------------------------------------------------------
    def f6(self):
        try:
            from tests.test_guard import guard, req
        except Exception as e:  # noqa: BLE001
            return "SKIP", f"backend not importable here ({type(e).__name__})"
        g = guard()

        def broken(rec):
            raise OSError("database is locked")

        g.history.record_check = broken
        import logging
        logging.disable(logging.CRITICAL)  # the Guard logs the (expected) write failure
        try:
            r = g.check(req())
        finally:
            logging.disable(logging.NOTSET)
        ev = f"in process (a disk fault cannot be caused remotely): history write fails -> {r.verdict.verdict.value}"
        return ("PASS" if r.verdict.verdict.value == "BLOCK" else "FAIL"), ev

    # ---- F7 one simulation at a time ---------------------------------------------------------
    def f7(self):
        if self.quick:
            return "SKIP", "--quick (two load simulations)"
        ok, why = self.t.ram_ok()
        if not ok:
            return "SKIP", why
        out = {}

        def sim(i):
            out[i] = self.t.req("POST", "/loadtest/run?normal=10", self.t.visitor(self.t.ipv4(70 + i)), timeout=240)[0]

        ts = [threading.Thread(target=sim, args=(i,)) for i in (1, 2)]
        ts[0].start()
        time.sleep(0.5)
        ts[1].start()
        [t.join(300) for t in ts]
        ev = f"two simulations from two visitors: {out.get(1)}, {out.get(2)}"
        if sorted(out.values()) == [200, 409]:
            return "KNOWN", ev + " (one at a time for everyone; Honest limits)"
        return ("PASS" if list(out.values()) == [200, 200] else "FAIL"), ev

    # ---- F8, F9, F10 ---------------------------------------------------------------------------
    def f8(self):
        want = {"content-security-policy": "frame-ancestors 'none'", "x-frame-options": "DENY",
                "x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}
        miss = []
        for path in ("/", "/status"):
            h = self.t.req("GET", path, self.t.visitor(self.t.ipv4(8)))[1]
            miss += [f"{path} {k}" for k, v in want.items() if v not in h.get(k, "")]
        return ("PASS" if not miss else "FAIL"), ("CSP, X-Frame-Options DENY, nosniff, no-referrer on / and /status"
                                                  if not miss else "missing: " + ", ".join(miss))

    def f9(self):
        got = {p: self.t.req("GET", p, self.t.visitor(self.t.ipv4(9)))[0] for p in ("/docs", "/redoc", "/openapi.json")}
        return ("PASS" if all(v == 404 for v in got.values()) else "FAIL"), ", ".join(f"{k} {v}" for k, v in got.items())

    def f10(self):
        t = self.t
        big = t.req("POST", "/demo/reset", t.visitor(t.ipv4(10)), b'{"run_ids": []' + b" " * 70_000 + b"}")[0]
        hdr = t.visitor(t.ipv4(10))
        name, value = next(iter(hdr.items()))
        line = t.raw((f"POST /demo/reset HTTP/1.1\r\nHost: {t.host}:{t.port}\r\n{name}: {value}\r\n"
                      "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                      "f\r\n{\"run_ids\": []}\r\n0\r\n\r\n").encode()).split("\r\n")[0]
        ev = f"70 KB body -> {big}; chunked body -> {line}"
        return ("PASS" if big == 413 and " 411 " in line + " " else "FAIL"), ev

    # ---- F11 unlimited endpoints ---------------------------------------------------------------
    def f11(self):
        t = self.t
        parts, bad = [], False
        ip = t.ipv4(11)
        n429 = None
        for i in range(1, 801):  # the general cap is 600/min
            if t.req("GET", "/health", t.visitor(ip))[0] == 429:
                n429 = i
                break
        parts.append(f"general cap: 429 at request {n429}")
        bad |= n429 is None
        if "run_a" in self.shared:
            hdr = t.visitor(self.shared["ip_a"])
            name, value = next(iter(hdr.items()))
            socks, codes = [], []
            for _ in range(6):
                s = socket.create_connection((t.host, t.port), timeout=10)
                s.sendall((f"GET /stream?run_id={self.shared['run_a']} HTTP/1.1\r\nHost: {t.host}:{t.port}\r\n"
                           f"{name}: {value}\r\n\r\n").encode())
                codes.append(int(s.recv(64).split(b" ")[1]))
                socks.append(s)
            for s in socks:
                s.close()
            parts.append(f"6 streams from one visitor: {codes}")
            bad |= 429 not in codes
        if t.admin:
            s, evs = t.json("GET", "/events?after=0", t.admin_headers(t.ipv4(121)))
            n = len(evs) if isinstance(evs, list) else -1
            parts.append(f"/events?after=0 reply: {n} events (cap 500)")
            bad |= not (0 <= n <= 500)
        return ("FAIL" if bad else "PASS"), "; ".join(parts)

    # ---- F12 admin token guesses ---------------------------------------------------------------
    def f12(self):
        t = self.t
        ip = t.ipv4(12)
        locked = None
        for i in range(1, 51):
            if t.req("GET", "/history", {**t.visitor(ip), "X-Admin-Token": f"guess{i:059d}"})[0] == 429:
                locked = i
                break
        ev = f"wrong tokens: 429 at guess {locked}"
        if t.admin:
            right = t.req("GET", "/history", t.admin_headers(ip))[0]
            ev += f"; then the right token from the same address -> {right}"
            return ("PASS" if locked and right == 429 else "FAIL"), ev
        return ("PASS" if locked else "FAIL"), ev

    # ---- F13, F14 (accepted) ---------------------------------------------------------------------
    def f13(self):
        out = self.shared.get("replay_f")
        if not out:
            return "SKIP", "needs the replay of F2"
        signed = any((r.get("approval") or {}).get("signature") for r in out.get("results", []))
        return ("KNOWN" if signed else "PASS"), ("the replay response carries Guard approval signatures (demo requests, "
                                                 "120 s expiry; Honest limits)" if signed else "no signatures in replays")

    def f14(self):
        if not self.t.admin:
            return "SKIP", "no ADMIN_TOKEN in .env (/check is an admin action)"
        body = {"wallet_type": "customer_hot", "chain_id": 1, "from": HOT, "to": rnd_address(), "data": "0x",
                "value": "\u0661\u0660\u0660"}
        s = self.t.req("POST", "/check", self.t.admin_headers(self.t.ipv4(122)), body, timeout=180)[0]
        ev = f"/check with Arabic-Indic digits as the amount -> {s}"
        return ("KNOWN" if s == 200 else "PASS" if s == 422 else "FAIL"), ev + (" (lenient input; Honest limits)" if s == 200 else "")

    # ---- F15, F16, F17 -------------------------------------------------------------------------------
    def f15(self):
        if self.t.proxy != "cloudflared":
            return "SKIP", "only for TRUSTED_PROXY=cloudflared (Plan B)"
        s, body = self.t.json("GET", "/status", {"Host": "fail-closed-demo.trycloudflare.com"})
        ok = s == 400
        return ("PASS" if ok else "FAIL"), f"a tunnel request without CF-Connecting-IP -> {s}"

    def f16(self):
        res = []
        for origin in ("http://localhost:5173", "https://evil.example"):
            h = self.t.req("OPTIONS", "/check", {**self.t.visitor(self.t.ipv4(16)), "Origin": origin,
                                                  "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "x-admin-token"})[1]
            res.append((origin, h.get("access-control-allow-origin", "none")))
        return ("PASS" if all(v == "none" for _, v in res) else "FAIL"), "; ".join(f"preflight from {o}: allow-origin {v}" for o, v in res)

    def f17(self):
        v = self.t.visitor(self.t.ipv4(17))
        got = {p: self.t.req("GET", p, v)[0] for p in ("/history", "/pending", "/breaker", "/events?after=0&run_id=main")}
        return ("PASS" if all(x in (401, 404) for x in got.values()) else "FAIL"), ", ".join(f"{k} {x}" for k, x in got.items())

    # ---- F18 what held ------------------------------------------------------------------------------
    def f18(self):
        t = self.t
        v = t.visitor(t.ipv4(18))
        leaks = []
        markers = ("ADMIN_TOKEN=", "VULTR_INFERENCE", "Traceback", "import ", "def ", "SQLite format", "C:\\", "/home/")
        for p in ("/.env", "/.git/HEAD", "/../.env", "/%2e%2e/.env", "/%2e%2e%2f.env", "/..%2f..%2f.env",
                  "/assets/../../.env", "/assets/%2e%2e/%2e%2e/.env", "/backend/app/api.py", "/data/planb/history.sqlite3"):
            s, h, b = t.req("GET", p, v)
            if s == 200 and not b.lstrip().lower().startswith("<!doctype html") or any(m in b for m in markers):
                leaks.append(f"{p} {s}")
        s, h, index = t.req("GET", "/", v)
        assets = sorted({part.split('"')[0] for part in index.split('/assets/')[1:]})
        bundle = index
        for a in assets:
            s, h, b = t.req("GET", "/assets/" + a, v)
            bundle += b
            if a.endswith(".js"):
                ms, mh, mb = t.req("GET", "/assets/" + a + ".map", v)
                if ms == 200 and '"mappings"' in mb:
                    leaks.append(f"source map {a}.map")
        secret_in_bundle = any(sv in bundle for sv in t.secrets)
        if secret_in_bundle:
            leaks.append("a secret is in the web bundle")
        s, h, b = t.req("POST", "/demo/reset", v, b"{not json")
        if any(m in b for m in markers):
            leaks.append("error reply shows internals")
        ev = (f"10 traversal paths, {len(assets)} bundle files and their source maps, a malformed body: "
              + ("nothing leaked" if not leaks else "LEAK: " + ", ".join(leaks)))
        return ("PASS" if not leaks else "FAIL"), ev

    # ---- red team ---------------------------------------------------------------------------------------
    def rt(self):
        if not self.redteam or self.quick:
            return "SKIP", "--quick / --skip-redteam"
        if not self.t.admin:
            return "SKIP", "no ADMIN_TOKEN in .env (the red team is an admin action)"
        ok, why = self.t.ram_ok()
        if not ok:
            return "SKIP", why
        s, h, body = self.t.req("POST", "/redteam/run", {**self.t.admin_headers(self.t.ipv4(123)),
                                                         "Accept": "text/event-stream"}, {}, timeout=900)
        if s != 200:
            return "FAIL", f"/redteam/run answered {s}"
        payloads = []
        for block in body.split("\n\n"):
            if "event: redteam_payload" in block:
                d = json.loads(block.split("data: ", 1)[1])
                payloads.append((d["name"], d["contained"]))
        contained = [n for n, c in payloads if c == "yes"]
        ev = f"{len(contained)} of {len(payloads)} contained: " + ", ".join(f"{n} {c}" for n, c in payloads)
        return ("PASS" if payloads and len(contained) == len(payloads) else "FAIL"), ev


ATTACKS = [
    ("P0", "the instance runs in public demo mode", "p0"),
    ("F2", "act on another visitor's demo (events, stream, approve, reset)", "f2"),
    ("F3", "bypass per-IP limits with IPv6 (/64) and ::ffff: addresses", "f3"),
    ("F4", "spend the AI budget by repeating one replay", "f4"),
    ("F5", "push other visitors' demos out by flooding runs", "f5"),
    ("F6", "get an approval while the history write fails", "f6"),
    ("F8", "frame the page / no CSP (security headers)", "f8"),
    ("F9", "read the API map (/docs, /redoc, /openapi.json)", "f9"),
    ("F10", "send a huge or chunked request body", "f10"),
    ("F11", "unlimited requests, streams, large /events replies", "f11"),
    ("F12", "guess the admin token without a limit", "f12"),
    ("F13", "replay responses carry real Guard approvals", "f13"),
    ("F15", "send tunnel requests without a visitor address", "f15"),
    ("F16", "call the API from another web origin (CORS)", "f16"),
    ("F17", "read the Guard's own history, queue, breaker, events", "f17"),
    ("F18", "path traversal, source maps, secrets in the bundle, error leaks", "f18"),
    ("F14", "lenient input (non-ASCII digits as an amount)", "f14"),
    ("F1", "parallel withdrawals skip the history rules", "f1"),
    ("F7", "one simulation at a time for everyone", "f7"),
    ("RT", "red team: 7 hostile payloads in real sandboxes", "rt"),
]


def print_table(rows, markdown: bool) -> None:
    if markdown:
        print("| ID | Attack | Result | Evidence |\n|---|---|---|---|")
        for r in rows:
            print("| " + " | ".join(x.replace("|", "/") for x in r) + " |")
        return
    w = [max(len(r[i]) for r in rows + [("ID", "Attack", "Result", "")]) for i in range(3)]
    print(f"{'ID':{w[0]}}  {'Attack':{w[1]}}  {'Result':{w[2]}}  Evidence")
    for r in rows:
        print(f"{r[0]:{w[0]}}  {r[1]:{w[1]}}  {r[2]:{w[2]}}  {r[3]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--proxy", choices=["cloudflared", "caddy"], default="cloudflared",
                    help="how the instance reads the visitor address (Plan B: cloudflared; VM: caddy)")
    ap.add_argument("--env", default=str(REPO / ".env"), help="where ADMIN_TOKEN is read (never printed)")
    ap.add_argument("--quick", action="store_true", help="skip the red team, the simulations and F1")
    ap.add_argument("--skip-redteam", action="store_true")
    ap.add_argument("--markdown", action="store_true", help="print the table as Markdown")
    a = ap.parse_args(argv)
    host = urlparse(a.url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("refusing: the suite attacks only an instance on this machine (127.0.0.1)")
        return 2
    envp = Path(a.env)
    admin = env_value(envp, "ADMIN_TOKEN")
    t = Target(a.url, a.proxy, admin, [admin, env_value(envp, "VULTR_INFERENCE_API_KEY")])
    try:
        t.req("GET", "/health", timeout=5)
    except OSError:
        print(f"no instance at {a.url}: start it first (e.g. .\\deploy\\planb.ps1 start)")
        return 2
    t.ram_ok()
    s = Suite(t, a.quick, not a.skip_redteam)
    print(f"security suite against {a.url} (proxy {a.proxy}, admin checks {'on' if admin else 'off'})")
    t0 = time.time()
    for fid, attack, name in ATTACKS:
        s.run(fid, attack, getattr(s, name))
        t.ram_ok()
    print()
    print_table(s.rows, a.markdown)
    fails = [r for r in s.rows if r[2] == "FAIL"]
    c = Counter(r[2] for r in s.rows)
    ram = f"{t.lowest_ram:.2f} GB" if t.lowest_ram is not None else "unknown"
    print(f"\n{c['PASS']} PASS, {c['FAIL']} FAIL, {c['KNOWN']} KNOWN, {c['SKIP']} SKIP in {time.time() - t0:.0f} s; "
          f"lowest free RAM {ram}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
