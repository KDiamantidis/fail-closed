"""Red-team containment check: hostile payloads in the real sandbox.

Every payload runs in a fresh container with EXACTLY the same options as a normal check
(container_options(): no network, read-only root, tmpfs, 512 MiB without swap, 1 CPU,
128 pids, cap-drop ALL, no-new-privileges, no env, no mounts). Only the command differs.
The container is destroyed afterwards, always.

Each payload is judged by CONTENT (what it printed, cgroup counters, docker state), never
by exit codes. A payload that is not contained is RED and makes the report fail.
While the fork bomb and the memory bomb run, the API health endpoint is called every
0.5 s to show that the host keeps answering.
"""

import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from sandbox_runner import LABEL, container_options

SECRET_NAME = re.compile(r"^[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSW|VULTR|ADMIN|PRIVATE)[A-Z0-9_]*=", re.M)


@dataclass
class Observed:
    output: str = ""
    exit_code: int | None = None
    oom_killed: bool = False
    timed_out: bool = False
    stopped_by_host: bool = False
    seconds: float = 0.0
    max_pids: int = 0
    max_mem: int = 0
    health: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Payload:
    name: str
    tried: str
    limit: str  # what is supposed to stop it
    script: str
    judge: Callable[["Observed", "Context"], tuple[bool, str]]
    run_seconds: float | None = None  # host stops it after this (None = until exit / hard timeout)
    watch_health: bool = False


@dataclass
class Context:
    settings: Settings
    secrets: list[str]  # real secret values on the host; none may appear in any output
    pids_limit: int
    mem_limit: int


def _lines(out: str, prefix: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith(prefix)]


# ---- payloads ------------------------------------------------------------------------

NETWORK = r'''
try() { if timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then echo "NET $3 OPEN"; else echo "NET $3 BLOCKED"; fi; }
try 1.1.1.1 443 internet-1.1.1.1:443
try 172.17.0.1 2375 docker-host-gateway-172.17.0.1:2375
try 172.17.0.1 22 docker-host-gateway-172.17.0.1:22
try host.docker.internal 80 host.docker.internal:80
try 169.254.169.254 80 cloud-metadata-169.254.169.254:80
if timeout 3 getent hosts vultr.com >/dev/null 2>&1; then echo "DNS vultr.com RESOLVED"; else echo "DNS vultr.com BLOCKED"; fi
echo "IFACES $(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | tr '\n' ' ')"
echo "ROUTES $(tail -n +2 /proc/net/route | wc -l)"
'''


def judge_network(o: Observed, c: Context) -> tuple[bool, str]:
    net = _lines(o.output, "NET ") + _lines(o.output, "DNS ")
    opened = [ln for ln in net if ln.endswith("OPEN") or ln.endswith("RESOLVED")]
    ifaces = (_lines(o.output, "IFACES ") or ["IFACES ?"])[0].split()[1:]
    routes = (_lines(o.output, "ROUTES ") or ["ROUTES ?"])[0].split()[-1]
    ok = len(net) == 6 and not opened and ifaces == ["lo"] and routes == "0"
    return ok, (f"{len(net) - len(opened)}/{len(net)} attempts blocked; interfaces: {' '.join(ifaces)}; "
                f"routes: {routes}" + (f"; OPEN: {opened}" if opened else ""))


def secrets_script(guard_key_path: str) -> str:
    return rf'''
echo "== env"; env
echo "== /proc/1/environ"; tr '\0' '\n' < /proc/1/environ 2>&1 || echo "cannot read"
echo "== find"; find / -xdev \( -name '.env' -o -name 'docker.sock' -o -name '*.key' -o -name '*.pem' \) \
  -not -path '/proc/*' -not -path '/etc/ssl/*' -not -path '/usr/share/*' -not -path '/usr/lib/*' 2>/dev/null
echo "== end find"
for s in /var/run/docker.sock /run/docker.sock; do [ -e "$s" ] && echo "SOCKET $s PRESENT" || echo "SOCKET $s ABSENT"; done
[ -e '{guard_key_path}' ] && echo "GUARDKEY PRESENT" || echo "GUARDKEY ABSENT"
'''


def judge_secrets(o: Observed, c: Context) -> tuple[bool, str]:
    out = o.output
    leaked = [s for s in c.secrets if s and s in out]
    names = SECRET_NAME.findall(out)
    found = out.split("== find", 1)[-1].split("== end find", 1)[0].strip().splitlines() if "== find" in out else ["?"]
    sockets = _lines(out, "SOCKET ")
    ok = (not leaked and not names and found == [] and len(sockets) == 2
          and all(s.endswith("ABSENT") for s in sockets) and "GUARDKEY ABSENT" in out)
    env_n = len(out.split("== env", 1)[-1].split("== /proc", 1)[0].strip().splitlines())
    return ok, (f"{env_n} env vars, none secret; host secret values found: {len(leaked)}; "
                f".env/docker.sock/*.key/*.pem found: {len(found)}; docker.sock absent; Guard key absent"
                + (f"; LEAK: {names or leaked or found}" if not ok else ""))


WRITE = r'''
for d in / /etc /usr /sandbox /home; do if touch "$d/fc_probe" 2>/dev/null; then echo "WRITE $d OPEN"; else echo "WRITE $d BLOCKED"; fi; done
if echo x > /tmp/fc_probe 2>/dev/null; then echo "TMP writable"; fi
cp /usr/bin/true /tmp/fc_true 2>/dev/null && if /tmp/fc_true 2>/dev/null; then echo "EXEC /tmp OPEN"; else echo "EXEC /tmp BLOCKED"; fi
'''


def judge_write(o: Observed, c: Context) -> tuple[bool, str]:
    w = _lines(o.output, "WRITE ")
    ok = len(w) == 5 and all(x.endswith("BLOCKED") for x in w) and "EXEC /tmp BLOCKED" in o.output
    return ok, (f"{sum(x.endswith('BLOCKED') for x in w)}/{len(w)} paths read-only; "
                f"/tmp is a small tmpfs and noexec ({'blocked' if 'EXEC /tmp BLOCKED' in o.output else 'EXEC OPEN'})")


# The main shell keeps running on builtins only (no fork needed) and reads the cgroup
# counters once a second: pids.current and pids.events ("max N" = forks refused at the limit).
FORK = r'''
echo "PIDS_MAX $(cat /sys/fs/cgroup/pids.max)"
f() { f | f & }
f 2>/dev/null
i=0
while [ $i -lt 40 ]; do
  n=0; while [ $n -lt 2000 ]; do n=$((n+1)); done
  read cur < /sys/fs/cgroup/pids.current; read ev < /sys/fs/cgroup/pids.events
  echo "PIDS_NOW $cur REFUSED $ev"
  i=$((i+1))
done
'''


def judge_fork(o: Observed, c: Context) -> tuple[bool, str]:
    pmax = (_lines(o.output, "PIDS_MAX ") or ["PIDS_MAX ?"])[0].split()[-1]
    samples = [re.match(r"PIDS_NOW (\d+) REFUSED max (\d+)", ln) for ln in _lines(o.output, "PIDS_NOW ")]
    samples = [(int(m.group(1)), int(m.group(2))) for m in samples if m]
    peak = max([s[0] for s in samples] + [o.max_pids], default=0)
    refused = max((s[1] for s in samples), default=0)
    hit = refused > 0 or peak >= c.pids_limit
    ok = pmax == str(c.pids_limit) and 0 < peak <= c.pids_limit and hit
    refused_txt = f"{refused} forks refused by the kernel at the limit" if samples else \
        "in-container counter starved of CPU (peak from docker stats)"
    return ok, (f"pids.max {pmax}; peak {peak} processes (limit {c.pids_limit}); {refused_txt}; "
                f"stopped by the host after {o.seconds:.0f}s, container destroyed")


MEMORY = r'''
echo "MEM_MAX $(cat /sys/fs/cgroup/memory.max) SWAP_MAX $(cat /sys/fs/cgroup/memory.swap.max 2>/dev/null)"
perl -e '$x = "x" x (2000*1024*1024); print "ALLOCATED 2000 MiB\n"'
echo "PERL_EXIT $?"
echo "MEMORY_EVENTS $(tr '\n' ' ' < /sys/fs/cgroup/memory.events)"
'''


def judge_memory(o: Observed, c: Context) -> tuple[bool, str]:
    ev = (_lines(o.output, "MEMORY_EVENTS ") or [""])[0]
    m = re.search(r"oom_kill (\d+)", ev)
    oom = int(m.group(1)) if m else 0
    mmax = (_lines(o.output, "MEM_MAX ") or ["MEM_MAX ? SWAP_MAX ?"])[0].split()
    ok = ("ALLOCATED" not in o.output and (oom >= 1 or o.oom_killed) and mmax[1] == str(c.mem_limit)
          and o.max_mem <= c.mem_limit)
    peak = f"; peak seen by docker stats {o.max_mem // 2**20} MiB" if o.max_mem else ""
    return ok, (f"memory.max {mmax[1]} (swap {mmax[3] if len(mmax) > 3 else '?'}); 2000 MiB allocation "
                f"{'NOT completed' if 'ALLOCATED' not in o.output else 'COMPLETED'}; the kernel OOM-killed "
                f"it ({oom} oom_kill event{'s' if oom != 1 else ''}){peak}")


CPU = r'''
echo "CPU_MAX $(cat /sys/fs/cgroup/cpu.max)"
while :; do :; done
'''


def judge_cpu(o: Observed, c: Context) -> tuple[bool, str]:
    cmax = (_lines(o.output, "CPU_MAX ") or ["CPU_MAX ? ?"])[0].split()[1:]
    one_cpu = cmax == ["100000", "100000"]
    ok = o.timed_out and one_cpu
    return ok, (f"cpu.max {' '.join(cmax)} (1 CPU); stopped by the hard timeout after {o.seconds:.0f}s "
                f"(SANDBOX_TIMEOUT_SECONDS={c.settings.sandbox_timeout_seconds:g}), container destroyed")


PRIVILEGE = r'''
grep -E '^(CapEff|CapPrm|CapBnd|NoNewPrivs)' /proc/self/status
if mount -t tmpfs none /mnt 2>/dev/null; then echo "MOUNT OPEN"; else echo "MOUNT BLOCKED"; fi
echo x > /tmp/fc_own; if chown 0:0 /tmp/fc_own 2>/dev/null; then echo "CHOWN OPEN"; else echo "CHOWN BLOCKED"; fi
echo "SU $(su -c 'id -u' root </dev/null 2>&1 | head -1)"
echo "SETPRIV $(setpriv --reuid=0 --regid=0 --clear-groups id -u 2>&1 | head -1)"
perl -e 'use Socket; if (socket(my $s, PF_INET, SOCK_RAW, 255)) { print "RAW OPEN\n" } else { print "RAW BLOCKED ($!)\n" }'
echo "UID $(id -u)"
'''


def judge_privilege(o: Observed, c: Context) -> tuple[bool, str]:
    out = o.output
    caps = dict(re.findall(r"^(CapEff|CapPrm|CapBnd):\s+([0-9a-f]+)", out, re.M))
    nnp = re.search(r"^NoNewPrivs:\s+(\d)", out, re.M)
    zero_caps = all(int(caps.get(k, "1"), 16) == 0 for k in ("CapEff", "CapPrm", "CapBnd"))
    root = re.search(r"^(SU|SETPRIV|UID) 0\s*$", out, re.M) is not None
    ok = (zero_caps and nnp is not None and nnp.group(1) == "1" and "MOUNT BLOCKED" in out
          and "CHOWN BLOCKED" in out and "RAW BLOCKED" in out and not root)
    return ok, (f"capabilities {'all zero' if zero_caps else caps}; NoNewPrivs {nnp.group(1) if nnp else '?'}; "
                f"mount {'blocked' if 'MOUNT BLOCKED' in out else 'OPEN'}; chown {'blocked' if 'CHOWN BLOCKED' in out else 'OPEN'}; "
                f"setuid su / setpriv {'never root' if not root else 'BECAME ROOT'}; "
                f"raw socket {'blocked' if 'RAW BLOCKED' in out else 'OPEN'}")


def payloads(settings: Settings) -> list[Payload]:
    return [
        Payload("network", "connect to 1.1.1.1:443, the Docker host gateway, host.docker.internal, cloud "
                "metadata 169.254.169.254; DNS lookup of vultr.com", "--network none", NETWORK, judge_network),
        Payload("secrets", "print env, read /proc/1/environ, search for .env, docker.sock, *.key, *.pem "
                "and the Guard key path", "no env, no mounts, no secrets in the image",
                secrets_script(settings.guard_key_path), judge_secrets),
        Payload("write", "write to /, /etc, /usr, /sandbox, /home; run a binary from /tmp",
                "--read-only root, /tmp tmpfs noexec", WRITE, judge_write),
        Payload("fork_bomb", "f() { f | f & }; f", f"--pids-limit {settings.sandbox_pids}", FORK, judge_fork,
                run_seconds=8, watch_health=True),
        Payload("memory_bomb", "allocate 2000 MiB", f"--memory {settings.sandbox_memory} (no swap): OOM kill",
                MEMORY, judge_memory, watch_health=True),
        Payload("cpu_spin", "while :; do :; done", f"--cpus {settings.sandbox_cpus:g} + hard timeout "
                f"{settings.sandbox_timeout_seconds:g}s", CPU, judge_cpu),
        Payload("privilege", "mount, chown to root, setuid su, setpriv to uid 0, raw socket",
                "--cap-drop ALL, no-new-privileges, non-root user", PRIVILEGE, judge_privilege),
    ]


def _mem_bytes(spec: str) -> int:
    units = {"k": 2**10, "m": 2**20, "g": 2**30}
    s = spec.strip().lower()
    return int(s[:-1]) * units[s[-1]] if s[-1] in units else int(s)


def host_secrets(settings: Settings) -> list[str]:
    """Real secret values on the host. The judge checks none of them reaches a container."""
    vals = [settings.vultr_inference_api_key, settings.admin_token]
    try:
        pem = Path(settings.guard_key_path).expanduser().read_text()
        vals += [ln for ln in pem.splitlines() if ln and not ln.startswith("-----")]
    except OSError:
        pass
    return [v for v in vals if v and len(v) >= 8]


class RedTeam:
    def __init__(self, settings: Settings, client: Any = None, health_probe: Callable[[], dict] | None = None,
                 gate: Any = None):
        self.settings = settings
        self._client = client
        self.health_probe = health_probe
        self.gate = gate

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def run(self, on_result: Callable[[dict], None] | None = None,
            only: list[str] | None = None) -> dict[str, Any]:
        ctx = Context(self.settings, host_secrets(self.settings), self.settings.sandbox_pids,
                      _mem_bytes(self.settings.sandbox_memory))
        started = time.time()
        results = []
        for p in payloads(self.settings):
            if only and p.name not in only:
                continue
            r = self._one(p, ctx)
            results.append(r)
            if on_result:
                on_result(r)
        opts = container_options(self.settings)
        return {
            "started": started, "finished": time.time(),
            "all_contained": bool(results) and all(r["contained"] for r in results),
            "red": [r["name"] for r in results if not r["contained"]],
            "limits": {k: opts[k] for k in ("network_mode", "read_only", "mem_limit", "memswap_limit",
                                            "nano_cpus", "pids_limit", "cap_drop", "security_opt", "tmpfs")}
                      | {"timeout_seconds": self.settings.sandbox_timeout_seconds},
            "results": results,
        }

    def _one(self, p: Payload, ctx: Context) -> dict[str, Any]:
        if self.gate is not None:
            with self.gate.slot():
                obs = self._execute(p)
        else:
            obs = self._execute(p)
        try:
            contained, result = p.judge(obs, ctx)
        except Exception as e:  # noqa: BLE001 - a judge that cannot decide is not "contained"
            contained, result = False, f"judge error: {e}"
        health = None
        if p.watch_health:
            ok = [h for h in obs.health if h.get("ok")]
            health = {"calls": len(obs.health), "answered": len(ok),
                      "max_ms": max((h["ms"] for h in ok), default=None),
                      "avg_ms": round(sum(h["ms"] for h in ok) / len(ok), 1) if ok else None}
            if self.health_probe is not None and len(ok) < len(obs.health):
                contained, result = False, result + f"; HOST HEALTH FAILED {len(obs.health) - len(ok)} time(s)"
        return {"name": p.name, "tried": p.tried, "limit": p.limit, "result": result,
                "contained": contained, "status": "contained" if contained else "RED",
                "seconds": round(obs.seconds, 1), "oom_killed": obs.oom_killed, "timed_out": obs.timed_out,
                "max_pids": obs.max_pids, "max_mem_mib": obs.max_mem // 2**20, "health": health,
                "output_tail": obs.output[-600:]}

    def _execute(self, p: Payload) -> Observed:
        obs = Observed()
        opts = container_options(self.settings)  # identical to a normal check
        stop = threading.Event()
        container = None
        t0 = time.perf_counter()
        try:
            container = self.client.containers.run(
                self.settings.sandbox_image, entrypoint=["sh", "-c"], command=[p.script],
                name=f"fc-redteam-{p.name}-{uuid.uuid4().hex[:8]}", **opts)
            watchers = [threading.Thread(target=self._watch_stats, args=(container, obs, stop), daemon=True)]
            if p.watch_health and self.health_probe is not None:
                watchers.append(threading.Thread(target=self._watch_health, args=(obs, stop), daemon=True))
            for w in watchers:
                w.start()
            limit = p.run_seconds if p.run_seconds is not None else self.settings.sandbox_timeout_seconds
            try:
                res = container.wait(timeout=limit)
                obs.exit_code = res.get("StatusCode") if isinstance(res, dict) else None
            except Exception:  # noqa: BLE001 - still running at the limit: the host stops it
                if p.run_seconds is not None:
                    obs.stopped_by_host = True
                else:
                    obs.timed_out = True
                try:
                    container.kill()
                except Exception:  # noqa: BLE001
                    pass
            obs.seconds = time.perf_counter() - t0
            stop.set()
            for w in watchers:
                w.join(timeout=5)
            try:
                container.reload()
                obs.oom_killed = bool(container.attrs.get("State", {}).get("OOMKilled"))
                obs.output = container.logs(stdout=True, stderr=True, tail=200).decode(errors="replace")
            except Exception as e:  # noqa: BLE001
                obs.output += f"\n(could not read logs: {e})"
        finally:
            stop.set()
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001
                    pass
        return obs

    @staticmethod
    def _watch_stats(container: Any, obs: Observed, stop: threading.Event) -> None:
        try:
            for s in container.stats(stream=True, decode=True):
                pids = (s.get("pids_stats") or {}).get("current") or 0
                mem = (s.get("memory_stats") or {}).get("usage") or 0
                obs.max_pids = max(obs.max_pids, int(pids))
                obs.max_mem = max(obs.max_mem, int(mem))
                if stop.is_set():
                    return
        except Exception:  # noqa: BLE001 - container gone: stats stream ends
            return

    def _watch_health(self, obs: Observed, stop: threading.Event) -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                r = self.health_probe()
                ok = bool(r.get("ok"))
            except Exception:  # noqa: BLE001
                ok = False
            obs.health.append({"t": round(time.perf_counter() - t0, 3), "ok": ok,
                               "ms": round((time.perf_counter() - t0) * 1000, 1)})
            stop.wait(0.5)


def label_filter() -> dict[str, str]:
    return {"label": LABEL}
