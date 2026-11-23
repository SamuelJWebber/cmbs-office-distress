"""Tests for the cmbs_distress CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmbs_distress.cli import main

FIXTURE = str(Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml")
FIXTURE_P2 = str(Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee_2026_10.xml")


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
    assert "longitudinal" not in data

    # Up-to-date file passes --check
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--check"]) == 0

    # Tampered file fails --check
    out.write_text("stale", encoding="utf-8")
    assert main(["build", "-i", FIXTURE, "-o", str(out), "--check"]) == 1


def test_cli_build_multi_period_longitudinal(tmp_path: Path):
    out = tmp_path / "index.html"
    summary = tmp_path / "summary.json"

    assert (
        main(["build", "-i", FIXTURE, FIXTURE_P2, "-o", str(out), "--summary-json", str(summary)])
        == 0
    )
    assert main(["build", "-i", FIXTURE, FIXTURE_P2, "-o", str(out), "--check"]) == 0
    html_text = out.read_text(encoding="utf-8")
    assert "Monthly Migration &amp; Appraisal Velocity" in html_text

    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["overall"]["balance"] == pytest.approx(618_000_000.0)
    assert payload["longitudinal"]["period_count"] == 2
    assert payload["longitudinal"]["latest_transition"]["special_servicing"]["inflow_count"] == 2


def test_cli_inspect_text_and_json(capsys: pytest.CaptureFixture[str]):
    assert main(["inspect", "-i", FIXTURE]) == 0
    out = capsys.readouterr().out
    assert "$640.0M" in out
    assert "Maturity wall" in out

    assert main(["inspect", "-i", FIXTURE, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["maturity_wall"]["balance"] == pytest.approx(444_000_000.0)

    # Multi-period inspect includes longitudinal summary
    assert main(["inspect", "-i", FIXTURE, FIXTURE_P2]) == 0
    multi_out = capsys.readouterr().out
    assert "Longitudinal:" in multi_out
    assert "SS inflows:  2 loans, $93.0M" in multi_out

    assert main(["inspect", "-i", FIXTURE, FIXTURE_P2, "--json"]) == 0
    multi_json = json.loads(capsys.readouterr().out)
    assert multi_json["longitudinal"]["latest_transition"]["appraisal_velocity"]["valuation_change"] == pytest.approx(
        -29_400_000.0
    )


def test_cli_compare_text_json_and_error(capsys: pytest.CaptureFixture[str]):
    assert main(["compare", "-i", FIXTURE, FIXTURE_P2]) == 0
    out = capsys.readouterr().out
    assert "2026-10-15 -> 2026-11-15" in out
    assert "SS inflows:" in out
    assert "-$29.4M" in out

    assert main(["compare", "-i", FIXTURE, FIXTURE_P2, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["latest_transition"]["losses_and_liquidations"]["incremental_loss"] == pytest.approx(
        7_500_000.0
    )

    # Single period compare returns exit code 1 with clear error
    assert main(["compare", "-i", FIXTURE]) == 1
    err = capsys.readouterr().err
    assert "at least 2" in err


def test_cli_check_contact(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CMBS_CONTACT", raising=False)
    assert main(["check-contact"]) == 1
    assert main(["check-contact", "--contact", "cmbs-office-distress ops@example.com"]) == 0
    assert main(["check-contact", "--contact", "bad (https://example.com)"]) == 1
