"""The web UI end to end in a real browser (Playwright + Chromium), against a real uvicorn
serving frontend/dist and real sandboxes.

Runs scenario (a) and (d) through the UI, then the containment panel, at desktop and phone
width, and fails on any browser console error. With FC_SCREENSHOTS=1 it also saves the
screenshots to docs/screenshots/.

Needs: Docker + sandbox image, `npm run build` in frontend/, `python -m playwright install chromium`.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "frontend" / "dist"
SHOTS = REPO / "docs" / "screenshots"

pytestmark = [pytest.mark.docker, pytest.mark.ui]
playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
if not (DIST / "index.html").exists():
    pytest.skip("frontend not built (cd frontend && npm run build)", allow_module_level=True)

VIEWPORTS = {"desktop": {"width": 1366, "height": 900}, "phone": {"width": 390, "height": 844}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="fc-ui-"))
    env = {**os.environ, "HISTORY_DB": str(tmp / "h.sqlite3"), "SIGNER_DB": str(tmp / "s.sqlite3"),
           "GUARD_KEY_PATH": str(tmp / "guard.pem"), "REDTEAM_PUBLIC": "true", "LLM_MODE": "mock",
           "MOCK_LLM_LATENCY_MS": "300", "ADMIN_TOKEN": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=REPO / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(base + "/status", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("API did not start: " + proc.stderr.read().decode(errors="replace")[-2000:])
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, base, viewport):
    page = browser.new_page(viewport=VIEWPORTS[viewport])
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(base + "/")
    page.wait_for_selector('[data-testid="chip-llm"] b:text("MOCK")')
    return page, errors


def shot(page, name: str, locator: str | None = None, bottom: str | None = None):
    """Save a screenshot (FC_SCREENSHOTS=1). `locator` goes near the top of the view; if
    `bottom` is given and fits, scroll just enough that it is fully visible too."""
    if os.environ.get("FC_SCREENSHOTS") != "1":
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    if locator:
        page.locator(locator).first.evaluate("el => el.scrollIntoView({block: 'start'})")
        page.evaluate("window.scrollBy(0, -12)")
    if bottom:
        page.locator(bottom).first.evaluate(
            "el => { const r = el.getBoundingClientRect(); const over = r.bottom - innerHeight + 16;"
            " if (over > 0) window.scrollBy(0, over); }")
    page.wait_for_timeout(250)  # let entry transitions settle
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def run_scenario(page, name: str):
    page.click(f'[data-testid="scenario-{name}"]')
    page.wait_for_selector('[data-testid="run"][data-status="done"]', timeout=60_000)


@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_ui_scenarios_and_containment(server, browser, viewport):
    page, errors = open_page(browser, server, viewport)
    shot(page, f"{viewport}-1-start")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    # (a) a small customer withdrawal: ALLOW, signed
    run_scenario(page, "a_customer_small_withdrawal")
    card = page.locator('[data-testid="request-card"]').first
    assert card.get_attribute("data-verdict") == "ALLOW"
    assert card.locator('[data-testid="signer-result"]').inner_text().strip() == "✓ Signed"

    # (d) Binance-style: BLOCK, refused, delegation + probe evidence, explanation arrives later
    run_scenario(page, "d_binance_7702")
    card = page.locator('[data-testid="request-card"]').first
    assert card.get_attribute("data-verdict") == "BLOCK"
    assert "Refused: no approval from the Guard" in card.locator('[data-testid="signer-result"]').inner_text()
    assert "EIP-7702 delegation" in card.locator('[data-testid="delegation-line"]').inner_text()
    assert "sweep() moved 100 ETH" in card.locator('[data-testid="probe-evidence"]').inner_text()
    page.wait_for_selector('[data-testid="explanation"]:has-text("MOCK")', timeout=20_000)
    assert page.locator('[data-testid="breaker"] [data-level="2"]').get_attribute("data-on") == "true"
    shot(page, f"{viewport}-2-scenario-d", '[data-testid="delegation-line"]')

    # containment panel (REDTEAM_PUBLIC=true here): 7 rows turn CONTAINED
    page.locator("#containment").scroll_into_view_if_needed()
    page.click('[data-testid="redteam-run"]')
    page.wait_for_selector('[data-testid="redteam-summary"]', timeout=90_000)
    statuses = page.locator('[data-testid^="payload-"]').evaluate_all("els => els.map(e => e.dataset.status)")
    assert statuses == ["CONTAINED"] * 7
    assert page.locator('[data-testid="redteam-summary"]').inner_text() == "All payloads contained."
    assert "answered" in page.locator('[data-testid="health-pings"]').inner_text()
    if viewport == "phone":
        assert page.locator(r'[data-testid="payload-network"] .sm\:hidden').inner_text().endswith("CONTAINED")
    shot(page, f"{viewport}-3-containment", "#containment",
         '[data-testid="redteam-summary"]' if viewport == "desktop" else None)

    assert errors == [], errors
    page.close()
