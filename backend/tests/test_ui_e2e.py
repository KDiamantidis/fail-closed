"""The web UI end to end in a real browser (Playwright + Chromium), against a real uvicorn
serving frontend/dist and real sandboxes.

At desktop and phone width: replays (a), (d), (e) and (f) through the UI, the review queue
(approve the honest large withdrawal), the blast radius card, the simulation and the
containment report. Fails on any browser console error or horizontal scroll. With
FC_SCREENSHOTS=1 it also saves the screenshots to docs/screenshots/.

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

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}


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
    page.goto(base + "/#/overview")
    page.wait_for_selector('[data-testid="replay-a_customer_small_withdrawal"]:not([disabled])')
    return page, errors


def no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), \
        f"horizontal scroll on {page.url}"


def go(page, route: str):
    page.evaluate(f"location.hash = {route!r}")
    page.wait_for_timeout(100)


def shot(page, name: str, locator: str | None = None, full: bool = False):
    """Save a screenshot (FC_SCREENSHOTS=1), with `locator` near the top of the view."""
    if os.environ.get("FC_SCREENSHOTS") != "1":
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    if locator:
        page.locator(locator).first.evaluate("el => el.scrollIntoView({block: 'start'})")
        page.evaluate("window.scrollBy(0, -(document.querySelector('header').offsetHeight + 12))")
    else:
        page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)  # let entry transitions settle
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def replay(page, name: str):
    """Run a replay from the Overview and open its signing requests."""
    go(page, "/overview")
    page.click(f'[data-testid="replay-{name}"]')
    page.locator(f'[data-testid="replay-outcome-{name}"] a').click(timeout=90_000)
    page.wait_for_selector('[data-testid="requests"]')


def open_first_request(page):
    page.locator('[data-testid^="request-row"]:visible').first.click()
    page.wait_for_selector('[data-testid="request-detail"]')


@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_ui_console(server, browser, viewport):
    page, errors = open_page(browser, server, viewport)
    no_hscroll(page)

    # (a) a small customer withdrawal: Approved, signed
    replay(page, "a_customer_small_withdrawal")
    open_first_request(page)
    detail = page.locator('[data-testid="request-detail"]')
    assert detail.get_attribute("data-verdict") == "ALLOW"
    assert page.locator('[data-testid="verdict-banner"]').inner_text().startswith("Approved")
    assert page.locator('[data-testid="signer-result"]').inner_text().strip() == "Signed"

    # (d) Binance-style: Blocked, signature refused, delegation + probe evidence, AI explains later
    replay(page, "d_binance_7702")
    open_first_request(page)
    assert detail.get_attribute("data-verdict") == "BLOCK"
    banner = page.locator('[data-testid="verdict-banner"]').inner_text()
    assert banner.startswith("Blocked: the wallet would hand control to an unknown contract")
    assert {"R1", "R2"} <= set(page.locator('[data-testid="rule-tag"]').all_inner_texts())
    assert page.locator('[data-testid="signer-result"]').inner_text().startswith(
        "Signature refused: no approval from the Guard")
    assert "Control handed over" in page.locator('[data-testid="delegation-line"]').inner_text()
    assert "sweep() moved 100 ETH" in page.locator('[data-testid="probe-evidence"]').inner_text()
    assert "AI explains, it never decides" in page.locator('[data-testid="ai-analysis"]').inner_text()
    page.wait_for_selector('[data-testid="ai-mock"]', timeout=20_000)
    page.wait_for_selector('[data-testid="ai-analysis"]:has-text("/100")')
    no_hscroll(page)
    shot(page, f"{viewport}-2-binance-detail", full=viewport == "phone")

    # (e) Mercado-style: the blast radius card
    replay(page, "e_mercado_multichain")
    br = page.locator('[data-testid="blast-radius"]')
    br.wait_for()
    assert page.locator('[data-testid="br-chains"]').inner_text().startswith("2 of 10")
    assert "ETH" in page.locator('[data-testid="br-released"]').inner_text()
    assert "ETH" in page.locator('[data-testid="br-held"]').inner_text()
    no_hscroll(page)
    shot(page, f"{viewport}-3-mercado-blast-radius", '[data-testid="blast-radius"]' if viewport == "phone" else None)

    # (f) honest large withdrawal: Needs review -> a person approves -> re-checked and signed
    replay(page, "f_honest_large_withdrawal")
    go(page, "/review")
    item = page.locator('[data-testid="review-item"]').filter(has_text="40 ETH to a customer")
    item.locator('[data-testid="approve"]').click()
    decision = item.locator('[data-testid="decision"]')
    decision.wait_for()
    assert decision.get_attribute("data-status") == "approved"
    assert "Signed" in decision.inner_text()
    # the Mercado replay's held requests, released by the hold worker (wait skipped in the demo)
    held = page.locator('[data-testid="held-list"]')
    assert "min skipped" in held.inner_text()
    no_hscroll(page)
    shot(page, f"{viewport}-4-review-queue")

    # Overview with the numbers filled in
    go(page, "/overview")
    assert int(page.locator('[data-testid="kpi-checked"] p').nth(1).inner_text()) >= 22
    assert page.locator('[data-testid="kpi-attacks"] p').nth(1).inner_text() != "0"
    no_hscroll(page)
    shot(page, f"{viewport}-1-overview")

    # Simulation: 50 withdrawals + 1 attack
    go(page, "/simulation")
    page.click('[data-testid="load-run"]')
    page.wait_for_selector('[data-testid="load-latency"]:has-text("requests/s")', timeout=120_000)
    assert page.locator('[data-testid="load-attack"] p').nth(1).inner_text() == "Yes"
    assert page.locator('[data-testid="load-affected"] p').nth(1).inner_text() == "0"
    assert page.locator('[data-testid="latency-chart"] svg circle').count() >= 51
    no_hscroll(page)
    shot(page, f"{viewport}-6-simulation")

    # Containment (REDTEAM_PUBLIC=true here): 7 checks Contained, host health measured
    go(page, "/containment")
    page.click('[data-testid="redteam-run"]')
    page.wait_for_selector('[data-testid="redteam-summary"]', timeout=120_000)
    statuses = page.locator('[data-testid^="payload-"]:visible').evaluate_all("els => els.map(e => e.dataset.status)")
    assert statuses == ["CONTAINED"] * 7
    assert page.locator('[data-testid="redteam-summary"]').inner_text().startswith("All 7 checks contained.")
    assert "Contained" in page.locator('[data-testid^="payload-"]:visible').first.inner_text()
    assert "/" in page.locator('[data-testid="health-pings"]').inner_text()
    no_hscroll(page)
    shot(page, f"{viewport}-5-containment")

    assert errors == [], errors
    page.close()
