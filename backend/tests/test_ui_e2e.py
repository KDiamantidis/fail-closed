"""The web UI end to end in a real browser (Playwright + Chromium), against a real uvicorn
serving frontend/dist and real sandboxes.

At desktop and phone width: the risk colour scale (level 0 Normal, level 2 High after the
delegation drain, level 4 Global stop after the coordinated attack, then the admin lift back to
Normal), replays (a), (d), (e), (f), (g) through the UI, plain step labels with the technical
label under "Technical detail", the review queue, the blast radius card, the simulation and the
containment report. Fails on any browser console error or horizontal scroll.

FC_SCREENSHOTS=1 saves the screenshots to docs/screenshots/. FC_LIVE_LLM=1 uses the real model
from .env (VULTR_INFERENCE_API_KEY) instead of the mock, for screenshots; the key stays in the
server process and is never printed.

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
ADMIN = "ui-test-admin-token"
LIVE = os.environ.get("FC_LIVE_LLM") == "1"

pytestmark = [pytest.mark.docker, pytest.mark.ui]
playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")
if not (DIST / "index.html").exists():
    pytest.skip("frontend not built (cd frontend && npm run build)", allow_module_level=True)

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(extra: dict | None = None):
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="fc-ui-"))
    env = {**os.environ, "HISTORY_DB": str(tmp / "h.sqlite3"), "SIGNER_DB": str(tmp / "s.sqlite3"),
           "GUARD_KEY_PATH": str(tmp / "guard.pem"), "REDTEAM_PUBLIC": "true", "ADMIN_TOKEN": ADMIN}
    if LIVE:
        env["LLM_MODE"] = "live"  # the key comes from .env inside the server process
    else:
        env.update({"LLM_MODE": "mock", "MOCK_LLM_LATENCY_MS": "300"})
    env.update(extra or {})
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
    return base, proc


@pytest.fixture(scope="module")
def server():
    base, proc = run_server()
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def poll_server():
    """Like Plan B behind a Cloudflare quick tunnel: public demo, updates by polling only."""
    base, proc = run_server({"LIVE_UPDATES": "poll", "PUBLIC_DEMO": "true", "DEPLOY_TARGET": "laptop"})
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
    page.wait_for_timeout(400)  # let entry transitions and the colour fade settle
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def replay(page, name: str):
    """Run a replay from the Overview and open its signing requests."""
    go(page, "/overview")
    page.click(f'[data-testid="replay-{name}"]')
    page.locator(f'[data-testid="replay-outcome-{name}"] a').click(timeout=120_000)
    page.wait_for_selector('[data-testid="requests"]')


def open_first_request(page):
    page.locator('[data-testid^="request-row"]:visible').first.click()
    page.wait_for_selector('[data-testid="request-detail"]')


def risk(page) -> tuple[str, str]:
    strip = page.locator('[data-testid="risk-strip"]')
    return strip.get_attribute("data-level"), page.locator('[data-testid="risk-sentence"]').inner_text()


def assert_level(page, level: int, sentence_start: str):
    page.wait_for_selector(f'[data-testid="risk-strip"][data-level="{level}"]', timeout=30_000)
    lvl, sentence = risk(page)
    assert lvl == str(level) and sentence.startswith(sentence_start), sentence
    # never colour alone: the strip carries text and an icon; the sidebar mark carries the number
    assert page.locator('[data-testid="risk-strip"] svg').count() == 1
    assert page.locator('[data-testid="nav-breaker-level"]:visible').first.inner_text().strip() == str(level)


def wait_for_explanation(page):
    if LIVE:
        page.wait_for_selector('[data-testid="ai-model"]', timeout=60_000)
    else:
        page.wait_for_selector('[data-testid="ai-mock"]', timeout=20_000)
    page.wait_for_selector('[data-testid="ai-analysis"]:has-text("/100")')


@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_ui_console(server, browser, viewport):
    page, errors = open_page(browser, server, viewport)
    no_hscroll(page)

    # level 0: calm, neutral, no banner
    assert_level(page, 0, "Normal: no limits in force")
    kpi = page.locator('[data-testid="kpi-risk"]')
    assert kpi.get_attribute("data-level") == "0" and page.locator('[data-testid="kpi-risk-name"]').inner_text() == "Normal"
    assert page.locator('[data-testid="global-stop-banner"]').count() == 0

    # first visit: the 60-second tour; Vultr named in the header chip, sidebar or footer
    assert page.locator('[data-testid="tour"]').inner_text().startswith("Try it in 60 seconds")
    vultr = "Runs on Vultr: Serverless Inference (AI) · Cloud Compute (Guard + sandboxes)"
    assert page.locator('[data-testid="vultr-footer"]').inner_text() == vultr
    if viewport == "desktop":
        assert page.locator('[data-testid="vultr-sidebar"]').inner_text() == vultr
        assert page.locator('[data-testid="pill-ai"]').inner_text() == (
            "AI: Vultr · glm-5.3-flash" if LIVE else "AI: Mock")
    else:  # the phone tab bar scrolls: the active tab stays in view
        go(page, "/policies")
        page.wait_for_timeout(300)
        box = page.locator('[data-testid="nav-policies"]').bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= VIEWPORTS["phone"]["width"]
        go(page, "/overview")

    # (a) a new customer gets a full simulation; the same customer again is approved on the rules alone
    replay(page, "a_customer_small_withdrawal")
    detail = page.locator('[data-testid="request-detail"]')
    seen = {}
    for i in range(2):
        go(page, "/requests")
        page.locator('[data-testid^="request-row"]:visible').nth(i).click()
        page.wait_for_selector('[data-testid="request-detail"]')
        assert detail.get_attribute("data-verdict") == "ALLOW"
        assert page.locator('[data-testid="verdict-banner"]').inner_text().startswith("Approved")
        assert page.locator('[data-testid="signer-result"]').inner_text().strip() == "Signed"
        step = page.locator('[data-testid="step-label"]').inner_text().removeprefix("Replay step: ")
        seen[step] = (page.locator('[data-testid="timeline-ai"]').inner_text(), detail.inner_text())
    first, again = seen["First payout to a new customer"], seen["Second payout to the same customer"]
    assert "not asked: new destination" in first[0] and "never paid before" in first[1]
    assert "not needed: known destination, small amount" in again[0]
    go(page, "/overview")  # the tour hides after the first replay; a link brings it back
    assert page.locator('[data-testid="tour"]').count() == 0 and page.locator('[data-testid="tour-show"]').is_visible()
    assert_level(page, 0, "Normal")
    go(page, "/overview")
    no_hscroll(page)
    shot(page, f"{viewport}-overview-level-0")

    # Policies: new destinations are simulated; the AI threshold is a visible policy value
    go(page, "/policies")
    page.wait_for_selector('[data-testid="policy-ai-pct"]')
    assert page.locator('[data-testid="policy-ai-pct"]').inner_text().startswith("10% of the wallet balance")
    assert page.locator('[data-testid="policy-new-dest"]').inner_text().startswith("Full simulation")

    # (d) hot wallet delegation drain: Blocked, wallet paused -> level 2 High
    replay(page, "d_delegation_drain")
    open_first_request(page)
    assert detail.get_attribute("data-verdict") == "BLOCK"
    banner = page.locator('[data-testid="verdict-banner"]').inner_text()
    assert banner.startswith("Blocked: the wallet would hand control to an unknown contract")
    assert {"R1", "R2"} <= set(page.locator('[data-testid="rule-tag"]').all_inner_texts())
    assert page.locator('[data-testid="signer-result"]').inner_text().startswith(
        "Signature refused: no approval from the Guard")
    assert "Control handed over" in page.locator('[data-testid="delegation-line"]').inner_text()
    assert "sweep() moved 100 ETH" in page.locator('[data-testid="probe-evidence"]').inner_text()
    # "Delegates to" instead of the wallet twice; plain words in the evidence, the demo
    # contract's real name only under "Technical detail"
    assert page.locator('[data-testid="delegates-to"]').inner_text() == "0x0000…eeee · unknown contract"
    evidence = page.locator('[data-testid="evidence"]')
    assert "attacker sweeper contract" in evidence.inner_text() and "MockSweeper" not in evidence.inner_text()
    assert "MockSweeper" in page.locator('[data-testid="evidence-technical"]').first.text_content()
    assert "checks: glm-5.3-flash · explanations: glm-5.3" in page.locator('[data-testid="ai-models"]').inner_text() \
        if LIVE else "Vultr Serverless Inference" in page.locator('[data-testid="ai-models"]').inner_text()
    # a plain step label; the technical one stays under the collapsed "Technical detail"
    assert page.locator('[data-testid="step-label"]').inner_text() == (
        "Replay step: Hot wallet asked to hand control to an unknown contract")
    tech = page.locator('[data-testid="step-technical"]')
    assert not tech.is_visible() and "type-4 tx with 5 authorizations" in tech.text_content()
    assert "AI explains, it never decides" in page.locator('[data-testid="ai-analysis"]').inner_text()
    wait_for_explanation(page)
    assert_level(page, 2, "High: 1 wallet paused")
    no_hscroll(page)
    shot(page, f"{viewport}-delegation-drain-detail", full=viewport == "phone")
    if viewport == "desktop" and os.environ.get("FC_SCREENSHOTS") == "1":
        # the crop on the submission cover (docs/brand/cover.html): 1000 px wide, so no sidebar
        page.set_viewport_size({"width": 1000, "height": 620})
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(REPO / "docs" / "brand" / "cover-ui.png"))
        page.set_viewport_size(VIEWPORTS["desktop"])
    go(page, "/overview")
    assert kpi.get_attribute("data-level") == "2" and page.locator('[data-testid="kpi-risk-name"]').inner_text() == "High"
    no_hscroll(page)
    shot(page, f"{viewport}-replay-level-2")

    # (e) multi-chain drain: the blast radius card; the address is frozen
    replay(page, "e_multichain_drain")
    br = page.locator('[data-testid="blast-radius"]')
    br.wait_for()
    assert page.locator('[data-testid="br-chains"]').inner_text().startswith("2 of 10")
    assert "ETH" in page.locator('[data-testid="br-released"]').inner_text()
    assert "ETH" in page.locator('[data-testid="br-held"]').inner_text()
    assert_level(page, 1, "Elevated: 1 address frozen")
    no_hscroll(page)

    # (f) honest large withdrawal: Needs review -> a person approves -> re-checked and signed
    replay(page, "f_honest_large_withdrawal")
    go(page, "/review")
    item = page.locator('[data-testid="review-item"]').filter(has_text="A customer withdraws 40% of the hot wallet")
    item.locator('[data-testid="approve"]').click()
    decision = item.locator('[data-testid="decision"]')
    decision.wait_for()
    assert decision.get_attribute("data-status") == "approved"
    assert "Signed" in decision.inner_text()
    held = page.locator('[data-testid="held-list"]')  # the multi-chain replay's held requests
    assert "min skipped" in held.inner_text()
    # one clear state per held row: blocked rows show no release time
    states = held.locator('[data-testid="held-state"]').all_inner_texts()
    assert "Blocked while held: never released" in states and "Not released: blocked" not in held.inner_text()
    blocked_rows = held.locator("li").filter(has_text="Blocked while held")
    assert all("release at" not in t for t in blocked_rows.all_inner_texts())
    assert_level(page, 0, "Normal")  # the latest replay paused nobody
    no_hscroll(page)
    shot(page, f"{viewport}-review-queue")

    # (g) coordinated attack: the colour climbs to level 4, the deep red banner, the admin lift
    go(page, "/overview")
    page.click('[data-testid="replay-g_coordinated_breaker"]')
    page.wait_for_selector('[data-testid="global-stop-banner"]', timeout=120_000)
    page.locator('[data-testid="replay-outcome-g_coordinated_breaker"]').wait_for(timeout=120_000)
    assert_level(page, 4, "Global stop: no withdrawals are being signed")
    assert page.locator('[data-testid="global-stop-banner"]').inner_text().startswith(
        "Global stop: no withdrawals are being signed")
    assert kpi.get_attribute("data-level") == "4"
    no_hscroll(page)
    shot(page, f"{viewport}-global-stop-level-4")
    # lifting it still needs the admin token
    page.fill('[data-testid="admin-token"]', "wrong-token")
    page.click('[data-testid="lift-global-stop"]')
    page.wait_for_selector('[data-testid="lift-error"]')
    assert risk(page)[0] == "4"
    page.fill('[data-testid="admin-token"]', ADMIN)
    page.click('[data-testid="lift-global-stop"]')
    assert_level(page, 0, "Normal")
    assert page.locator('[data-testid="global-stop-banner"]').count() == 0
    assert kpi.get_attribute("data-level") == "0"

    # "Reset demo" (public, no token): the visitor's own demo back to level 0
    page.click('[data-testid="replay-d_delegation_drain"]')
    page.locator('[data-testid="replay-outcome-d_delegation_drain"]').wait_for(timeout=120_000)
    assert_level(page, 2, "High")
    page.click('[data-testid="reset-demo"]')
    assert_level(page, 0, "Normal")
    assert page.locator('[data-testid="demo-notice"]').inner_text().startswith("Demo reset")
    assert page.locator('[data-testid="reset-demo"]').is_disabled()  # nothing left to reset
    # the replays again, so the KPIs below have numbers (the reset cleared them)
    for name in ("a_customer_small_withdrawal", "e_multichain_drain", "f_honest_large_withdrawal"):
        page.click(f'[data-testid="replay-{name}"]')
        page.locator(f'[data-testid="replay-outcome-{name}"]').wait_for(timeout=120_000)

    # Overview with the numbers filled in
    assert int(page.locator('[data-testid="kpi-checked"] p').nth(1).inner_text()) >= 22
    assert page.locator('[data-testid="kpi-attacks"] p').nth(1).inner_text() != "0"
    no_hscroll(page)

    # Simulation: 50 withdrawals + 1 attack
    go(page, "/simulation")
    page.click('[data-testid="load-run"]')
    page.wait_for_selector('[data-testid="load-latency"]:has-text("requests/s")', timeout=180_000)
    assert page.locator('[data-testid="load-attack"] p').nth(1).inner_text() == "Yes"
    assert page.locator('[data-testid="load-affected"] p').nth(1).inner_text() == "0"
    assert page.locator('[data-testid="latency-chart"] svg circle').count() >= 51
    no_hscroll(page)

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
    shot(page, f"{viewport}-containment")

    # the deliberate wrong admin token above is the only failed request the browser may log
    expected = "console.error: Failed to load resource: the server responded with a status of 403 (Forbidden)"
    assert errors.count(expected) <= 1 and [e for e in errors if e != expected] == [], errors
    page.close()


def test_reduced_motion_has_no_colour_animation(server, browser):
    page = browser.new_page(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    page.goto(server + "/#/overview")
    page.wait_for_selector('[data-testid="risk-strip"]')
    assert page.locator('[data-testid="risk-strip"]').evaluate("e => getComputedStyle(e).transitionDuration") == "0s"
    page.close()
    page = browser.new_page(viewport=VIEWPORTS["desktop"])
    page.goto(server + "/#/overview")
    page.wait_for_selector('[data-testid="risk-strip"]')
    assert page.locator('[data-testid="risk-strip"]').evaluate("e => getComputedStyle(e).transitionDuration").startswith("0.2s")
    page.close()


def follow_without_sse(page, base: str) -> list[str]:
    """Open the Overview and count the update requests (no /stream may be needed)."""
    calls: list[str] = []
    page.on("request", lambda r: calls.append(r.url.split(base, 1)[-1].split("?")[0]))
    page.goto(base + "/#/overview")
    page.wait_for_selector('[data-testid="replay-d_delegation_drain"]:not([disabled])')
    return calls


@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_forced_polling_shows_every_live_update(poll_server, browser, viewport):
    """LIVE_UPDATES=poll (a tunnel without SSE): the risk level rises, the AI card arrives,
    the global stop shows, Reset demo works; the page never opens /stream."""
    page = browser.new_page(viewport=VIEWPORTS[viewport])
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    calls = follow_without_sse(page, poll_server)
    assert page.locator('[data-testid="vultr-footer"]').inner_text() == (
        "AI runs on Vultr Serverless Inference. Guard and sandboxes run on the demo laptop "
        "(Vultr VM blocked by account review).")
    replay(page, "d_delegation_drain")
    assert page.locator('[data-testid="live-status"]').inner_text() in ("Updates every 2 s", "Updates delayed, retrying…")
    open_first_request(page)
    wait_for_explanation(page)  # the explanation card comes from polled events
    assert_level(page, 2, "High: 1 wallet paused")
    go(page, "/overview")
    page.click('[data-testid="replay-g_coordinated_breaker"]')
    page.wait_for_selector('[data-testid="global-stop-banner"]', timeout=120_000)
    page.locator('[data-testid="replay-outcome-g_coordinated_breaker"]').wait_for(timeout=120_000)
    assert_level(page, 4, "Global stop: no withdrawals are being signed")
    page.click('[data-testid="reset-demo"]')
    assert_level(page, 0, "Normal")
    no_hscroll(page)
    assert "/stream" not in calls and calls.count("/events") >= 3
    assert errors == [], errors
    page.close()


def test_a_blocked_stream_falls_back_to_polling(server, browser):
    """SSE first; when the stream does not get through, the page polls on its own."""
    page = browser.new_page(viewport=VIEWPORTS["desktop"])
    page.route("**/stream*", lambda route: route.abort())
    calls = follow_without_sse(page, server)
    replay(page, "d_delegation_drain")
    page.wait_for_selector('[data-testid="live-status"]')
    open_first_request(page)
    wait_for_explanation(page)
    assert_level(page, 2, "High")
    assert "/stream" in calls and "/events" in calls  # tried the stream, then polled
    page.close()


@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_public_demo_ui_works_under_the_csp(poll_server, browser, viewport):
    """F8 (security audit): in public mode the app sends the Caddyfile's security headers. Every
    page, a replay with its AI card, the review queue and Reset demo work with no CSP violation."""
    page = browser.new_page(viewport=VIEWPORTS[viewport])
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.add_init_script("document.addEventListener('securitypolicyviolation', e => "
                         "console.error('CSP violation: ' + e.violatedDirective + ' ' + e.blockedURI))")
    resp = page.goto(poll_server + "/#/overview")
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"
    page.wait_for_selector('[data-testid="replay-f_honest_large_withdrawal"]:not([disabled])')
    replay(page, "f_honest_large_withdrawal")
    open_first_request(page)
    wait_for_explanation(page)
    for route in ("/requests", "/review", "/breaker", "/containment", "/simulation", "/policies", "/overview"):
        go(page, route)
        page.wait_for_timeout(300)
        no_hscroll(page)
    page.click('[data-testid="reset-demo"]')
    assert_level(page, 0, "Normal")
    assert errors == [], errors
    page.close()
