"""No real company or incident names on the website: UI source, the built UI, the API text the
UI shows, and screenshot files. The README and docs/ keep them on purpose (the "Motivation"
section names the incidents that inspired the project); they are not checked here.
"""

import json
import re
from pathlib import Path

import pytest

from tests.api_helpers import FakeRunner, client

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"

# Exchanges and services from public incidents (word boundaries: "okx" must not match inside a word).
NAMES = ["Binance", "Mercado", "Bybit", "Coinbase", "Kraken", "OKX", "Upbit", "WazirX", "Bitget", "KuCoin",
         "Huobi", "HTX", "FTX", "Bitfinex", "Poloniex", "CoinEx", "DMM", "Ronin", "Mt\\.? ?Gox", "Crypto\\.com",
         "Atomic Wallet", "Bithumb", "BingX", "Phemex", "Lazarus"]
PATTERN = re.compile(r"\b(" + "|".join(NAMES) + r")\b", re.IGNORECASE)
TEXT = {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".svg", ".md", ".txt"}


def hits(text: str) -> list[str]:
    return sorted({m.group(0) for m in PATTERN.finditer(text)})


def ui_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix in TEXT and "node_modules" not in p.parts]


def test_the_pattern_finds_names():
    assert hits("a Binance-style drain") == ["Binance"] and hits("Mercado Bitcoin") == ["Mercado"]
    assert hits("token okx") == ["okx"] and hits("rockxyz, phtx, ftxs") == []


def test_ui_source_has_no_real_names():
    files = ui_files(FRONTEND / "src") + [FRONTEND / "index.html"]
    found = {str(p.relative_to(REPO)): h for p in files if (h := hits(p.read_text(encoding="utf-8")))}
    assert found == {}


def test_built_ui_has_no_real_names():
    dist = FRONTEND / "dist"
    if not (dist / "index.html").exists():
        pytest.skip("frontend not built (cd frontend && npm run build)")
    found = {str(p.relative_to(REPO)): h for p in ui_files(dist)
             if (h := hits(p.read_text(encoding="utf-8", errors="replace")))}
    assert found == {}


def test_screenshot_files_have_neutral_names():
    shots = REPO / "docs" / "screenshots"
    names = [p.name for p in shots.iterdir()] if shots.exists() else []
    assert [n for n in names if hits(n.replace("-", " ").replace("_", " "))] == []


def test_scenario_text_the_ui_shows_has_no_real_names():
    for p in sorted((REPO / "scenarios").glob("*.json")):
        sc = json.loads(p.read_text(encoding="utf-8"))
        shown = [p.stem, sc["name"], sc["title"], sc["description"], *sc.get("labels", {}).values()]
        for r in sc["requests"]:
            shown += [r["label"], r.get("plain", "")]
            assert r.get("plain"), f"{p.name}: every replay step needs a plain-language label"
        assert hits(" ".join(shown)) == [], p.name


def test_api_text_the_ui_shows_has_no_real_names(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    bodies = [c.get(path).text for path in ("/status", "/scenarios", "/policies", "/breaker", "/history")]
    for name in ("d_delegation_drain", "e_multichain_drain", "g_coordinated_breaker"):
        bodies.append(c.post(f"/scenarios/{name}/run").text)
    assert hits(" ".join(bodies)) == []


def test_new_scenario_names():
    titles = {p.stem: json.loads(p.read_text(encoding="utf-8"))["title"] for p in (REPO / "scenarios").glob("*.json")}
    assert titles["d_delegation_drain"] == "(d) Hot wallet delegation drain (EIP-7702)"
    assert titles["e_multichain_drain"] == "(e) Multi-chain drain after key theft"
