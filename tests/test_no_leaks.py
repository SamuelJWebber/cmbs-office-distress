"""Leak-prevention guardrails across the entire repository.

Enforces:
- Zero local Windows or macOS user directory paths.
- Zero personal email addresses (only RFC 2606 example domains allowed in docs/tests).
- Zero hardcoded API tokens, GitHub tokens, or private keys.
- Zero live SEC EDGAR HTTP calls in the test suite.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist"}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".txt",
    ".cfg",
    ".ini",
    "",
}

# Build sensitive patterns dynamically so this file never embeds literal secrets/emails.
_WIN_PATH_RE = re.compile(r"[A-Za-z]:[\\/]Us" + r"ers[\\/]", re.IGNORECASE)
_MAC_PATH_RE = re.compile(r"/Us" + r"ers/[A-Za-z0-9._-]+/")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}

_SECRET_PATTERNS = [
    ("GitHub token", re.compile(r"\b(gh" + r"p_|gh" + r"o_|github_" + r"pat_)[A-Za-z0-9_]{10,}")),
    ("AWS access key", re.compile(r"\bAK" + r"IA[0-9A-Z]{16}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-" + r"[A-Za-z0-9]{20,}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----")),
]


def _iter_repo_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore"):
            files.append(path)
    return sorted(files)


def test_no_local_paths_anywhere():
    hits: list[str] = []
    for path in _iter_repo_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _WIN_PATH_RE.search(text):
            hits.append(f"{rel}: contains Windows user path")
        if _MAC_PATH_RE.search(text):
            hits.append(f"{rel}: contains macOS user path")
    assert not hits, "Local path leaks detected:\n" + "\n".join(hits)


def test_no_personal_emails_anywhere():
    hits: list[str] = []
    for path in _iter_repo_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for match in _EMAIL_RE.finditer(text):
            domain = match.group(1).lower()
            if domain not in _ALLOWED_EMAIL_DOMAINS:
                hits.append(f"{rel}: unexpected email domain {domain!r}")
    assert not hits, "Personal/non-placeholder emails detected:\n" + "\n".join(hits)


def test_no_secrets_anywhere():
    hits: list[str] = []
    for path in _iter_repo_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: matched {label}")
    assert not hits, "Potential secrets detected:\n" + "\n".join(hits)


def test_tests_never_call_live_edgar_fetchers():
    tests_dir = REPO_ROOT / "tests"
    forbidden = ("fetch_bytes(", "fetch_submissions(", "download_exhibit(")
    hits: list[str] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == "test_no_leaks.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append(f"{path.name}: calls {token}")
    assert not hits, "Live EDGAR fetcher referenced in tests:\n" + "\n".join(hits)
