"""Unified application version/build metadata resolution.

Resolution order:
- version: installed package metadata, ``APP_VERSION`` env, ``build_info.json``, ``pyproject.toml``
- commit: local git, ``COMMIT_HASH``/``VITE_COMMIT_HASH`` env, ``build_info.json``

This keeps backend surfaces, release bundles, and Docker builds aligned.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

RELEASE_BUILD_INFO_FILENAME = "build_info.json"
PROJECT_NAME = "remoteterm-meshcore"


@dataclass(frozen=True)
class AppBuildInfo:
    version: str
    version_source: str
    commit_hash: str | None
    commit_source: str | None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_build_info(root: Path) -> dict[str, Any] | None:
    build_info_path = root / RELEASE_BUILD_INFO_FILENAME
    try:
        data = json.loads(build_info_path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _package_metadata_version() -> str | None:
    try:
        return importlib.metadata.version(PROJECT_NAME)
    except Exception:
        return None


def _env_version() -> str | None:
    value = os.getenv("APP_VERSION")
    return value.strip() if value and value.strip() else None


def _build_info_version(build_info: dict[str, Any] | None) -> str | None:
    if not build_info:
        return None
    value = build_info.get("version")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _pyproject_version(root: Path) -> str | None:
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text())
        project = pyproject.get("project")
        if isinstance(project, dict):
            version = project.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    except Exception:
        return None
    return None


def _git_output(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    output = result.stdout.strip()
    return output or None


def _env_commit_hash() -> str | None:
    for name in ("COMMIT_HASH", "VITE_COMMIT_HASH"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()[:8]
    return None


def _build_info_commit_hash(build_info: dict[str, Any] | None) -> str | None:
    if not build_info:
        return None
    value = build_info.get("commit_hash")
    return value.strip()[:8] if isinstance(value, str) and value.strip() else None


@lru_cache(maxsize=1)
def get_app_build_info() -> AppBuildInfo:
    root = repo_root()
    build_info = _read_build_info(root)

    version = _package_metadata_version()
    version_source = "package_metadata"
    if version is None:
        version = _env_version()
        version_source = "env"
    if version is None:
        version = _build_info_version(build_info)
        version_source = "build_info"
    if version is None:
        version = _pyproject_version(root)
        version_source = "pyproject"
    if version is None:
        version = "0.0.0"
        version_source = "fallback"

    commit_hash = _git_output(root, "rev-parse", "--short", "HEAD")
    commit_source: str | None = "git" if commit_hash else None
    if commit_hash is None:
        commit_hash = _env_commit_hash()
        commit_source = "env" if commit_hash else None
    if commit_hash is None:
        commit_hash = _build_info_commit_hash(build_info)
        commit_source = "build_info" if commit_hash else None

    return AppBuildInfo(
        version=version,
        version_source=version_source,
        commit_hash=commit_hash,
        commit_source=commit_source,
    )


def git_output(*args: str) -> str | None:
    """Shared git helper for debug surfaces that still need live repo state."""
    return _git_output(repo_root(), *args)
