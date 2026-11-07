"""The security suite (scripts/security_suite.py) as a pytest test, against a RUNNING instance.

    FC_SECURITY_URL=http://127.0.0.1:8000 pytest -m security          # Plan B (cloudflared)
    FC_SECURITY_PROXY=caddy FC_SECURITY_URL=... pytest -m security    # the VM

Skipped unless FC_SECURITY_URL is set: it never attacks an instance by accident."""

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.security
URL = os.environ.get("FC_SECURITY_URL", "")


@pytest.mark.skipif(not URL, reason="set FC_SECURITY_URL to attack a running instance")
def test_security_suite_finds_no_hole():
    path = Path(__file__).resolve().parents[2] / "scripts" / "security_suite.py"
    spec = importlib.util.spec_from_file_location("security_suite", path)
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    args = ["--url", URL, "--proxy", os.environ.get("FC_SECURITY_PROXY", "cloudflared")]
    if os.environ.get("FC_SECURITY_QUICK"):
        args.append("--quick")
    assert suite.main(args) == 0
