"""
Tests for manage_remoterm.sh CLI argument parsing and precondition guards.

All tests run as non-root, with no TTY, and with REMOTETERM_INSTALL_DIR
pointed at a controlled tmpdir. Only early-exit paths (before require_root,
require_tty, or ensure_pi) are covered here; full install/upgrade/uninstall
workflows require a Pi and interactive TTY.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "manage_remoterm.sh"


def run(args: list[str], install_dir: Path, stdin_data: bytes = b"") -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "REMOTETERM_INSTALL_DIR": str(install_dir),
        "TERM": "",  # suppress TTY detection so non-interactive guard triggers
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin_data,
        capture_output=True,
        env=env,
    )


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    """Simulates a clean system — nothing installed."""
    return tmp_path


@pytest.fixture()
def backend_only_dir(tmp_path: Path) -> Path:
    """Simulates a BE-only install (pyproject.toml present, no dist/index.html)."""
    (tmp_path / "pyproject.toml").write_text('version = "3.2.1"\n')
    return tmp_path


# ── Argument parsing: install ────────────────────────────────────────────────


def test_install_invalid_components_value(empty_dir: Path) -> None:
    result = run(["install", "--components", "foo"], empty_dir)
    assert result.returncode == 2
    assert b"unknown value" in result.stderr.lower() or b"unknown value" in result.stderr


def test_install_missing_components_value(empty_dir: Path) -> None:
    result = run(["install", "--components"], empty_dir)
    assert result.returncode == 2
    assert b"requires a value" in result.stderr.lower() or b"requires" in result.stderr


def test_install_unknown_flag(empty_dir: Path) -> None:
    result = run(["install", "--badarg"], empty_dir)
    assert result.returncode == 2
    assert b"unknown install argument" in result.stderr


def test_install_missing_version_value(empty_dir: Path) -> None:
    result = run(["install", "--components", "be", "--version"], empty_dir)
    assert result.returncode == 2
    assert b"requires a value" in result.stderr.lower() or b"requires" in result.stderr


def test_install_version_flag_accepted(empty_dir: Path) -> None:
    """Regression: --version must not be rejected as an unknown argument (was broken before fix)."""
    result = run(["install", "--components", "be", "--version", "latest"], empty_dir)
    assert b"unknown install argument" not in result.stderr
    assert result.returncode != 2


# ── Non-interactive mode guard ───────────────────────────────────────────────


def test_install_noninteractive_requires_components(empty_dir: Path) -> None:
    """install without --components and no TTY must exit 1 with guidance."""
    result = run(["install"], empty_dir, stdin_data=b"")
    assert result.returncode == 1
    assert b"--components is required in non-interactive mode" in result.stderr


# ── State-based preconditions ────────────────────────────────────────────────


def test_install_fe_without_backend(empty_dir: Path) -> None:
    """install --components fe with no backend must exit 1 with guidance."""
    result = run(["install", "--components", "fe"], empty_dir)
    assert result.returncode == 1
    assert b"backend is not installed" in result.stderr


def test_upgrade_nothing_installed(empty_dir: Path) -> None:
    """upgrade on a clean system must exit 1 with a clear message."""
    result = run(["upgrade"], empty_dir)
    assert result.returncode == 1
    assert b"not installed" in result.stderr.lower()


def test_uninstall_nothing_installed(empty_dir: Path) -> None:
    """uninstall on a clean system must exit 1 with a clear message."""
    result = run(["uninstall"], empty_dir)
    assert result.returncode == 1
    assert b"nothing to remove" in result.stderr
