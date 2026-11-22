"""Tests for the cmbs_distress CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmbs_distress.cli import main

FIXTURE = str(Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml")


def test_cli_build_and_check_cycle(tmp_path: Path):
    out = tmp_path / "index.html"
    summary = tmp_path / "summary.json"

    # Missing file fails --check
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--check"]) == 1

    # Build succeeds and emits summary JSON
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--summary-json", str(summary)]) == 0
    assert out.exists()
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["overall"]["loans"] == 16

    # Up-to-date file passes --check
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--check"]) == 0

    # Tampered file fails --check
    out.write_text("stale", encoding="utf-8")
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--check"]) == 1


def test_cli_inspect_text_and_json(capsys: pytest.CaptureFixture[str]):
    assert main(["inspect", "-i", FIXTURE]) == 0
    out = capsys.readouterr().out
    assert "$640.0M" in out
    assert "Maturity wall" in out

    assert main(["inspect", "-i", FIXTURE, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["maturity_wall"]["balance"] == pytest.approx(444_000_000.0)


def test_cli_check_contact(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CMBS_CONTACT", raising=False)
    assert main(["check-contact"]) == 1
    assert main(["check-contact", "--contact", "cmbs-office-distress ops@example.com"]) == 0
    assert main(["check-contact", "--contact", "bad (https://example.com)"]) == 1
