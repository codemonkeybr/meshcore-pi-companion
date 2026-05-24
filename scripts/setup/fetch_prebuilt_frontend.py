#!/usr/bin/env python3
"""
fetch_prebuilt_frontend.py

Downloads the latest prebuilt frontend artifact from the GitHub releases page
and installs it into frontend/prebuilt/ so the backend can serve it directly.

No GitHub CLI or authentication required — uses only the public releases API
and browser_download_url. Requires only the Python standard library.
"""

import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "jkingsman/Remote-Terminal-for-MeshCore"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PREBUILT_PREFIX = "Remote-Terminal-for-MeshCore/frontend/prebuilt/"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PREBUILT_DIR = REPO_ROOT / "frontend" / "prebuilt"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_prebuilt_asset(release: dict) -> tuple[str, str, str]:
    """Return (tag_name, asset_name, download_url) for the prebuilt zip."""
    tag = release.get("tag_name", "")
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("remoteterm-prebuilt-frontend-") and name.endswith(".zip"):
            return tag, name, asset["browser_download_url"]
    raise SystemExit(
        f"No prebuilt frontend artifact found in the latest release.\n"
        f"Check https://github.com/{REPO}/releases for available assets."
    )


def download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def extract_prebuilt(zip_path: Path, dest: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.startswith(PREBUILT_PREFIX)]
        if not members:
            raise SystemExit(f"'{PREBUILT_PREFIX}' not found inside zip.")

        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        for member in members:
            rel = member[len(PREBUILT_PREFIX):]
            if not rel:
                continue
            target = dest / rel
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    return len(members)


def main() -> None:
    print("Fetching latest release info...")
    release = fetch_json(API_URL)
    tag, asset_name, download_url = find_prebuilt_asset(release)
    print(f"  Release : {tag}")
    print(f"  Asset   : {asset_name}")
    print()

    zip_path = PREBUILT_DIR.parent / asset_name
    try:
        print(f"Downloading {asset_name}...")
        download(download_url, zip_path)

        print("Extracting prebuilt frontend...")
        count = extract_prebuilt(zip_path, PREBUILT_DIR)
        print(f"Extracted {count} entries.")
    finally:
        zip_path.unlink(missing_ok=True)

    print()
    print(f"Done! Prebuilt frontend ({tag}) installed to frontend/prebuilt/")
    print("Start the server with:")
    print("  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
