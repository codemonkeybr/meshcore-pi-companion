#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

usage() {
    cat <<'EOF'
Usage: scripts/build/create_github_release.sh --version X.Y.Z --asset PATH [options]

Options:
  --version VERSION         Release version / tag (required)
  --asset PATH              Asset to attach; may be specified multiple times
  --notes-file PATH         Markdown release notes file; defaults to CHANGELOG section
  --full-git-hash HASH      Commit to tag if the tag does not already exist locally
  --title TITLE             Release title (default: version)
  --help                    Show this message
EOF
}

VERSION=""
TITLE=""
NOTES_FILE=""
FULL_GIT_HASH=""
ASSETS=()
TEMP_NOTES_FILE=""

cleanup() {
    if [ -n "$TEMP_NOTES_FILE" ] && [ -f "$TEMP_NOTES_FILE" ]; then
        rm -f "$TEMP_NOTES_FILE"
    fi
}
trap cleanup EXIT

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --asset)
            ASSETS+=("${2:-}")
            shift 2
            ;;
        --notes-file)
            NOTES_FILE="${2:-}"
            shift 2
            ;;
        --full-git-hash)
            FULL_GIT_HASH="${2:-}"
            shift 2
            ;;
        --title)
            TITLE="${2:-}"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            release_die "Unknown argument: $1"
            ;;
    esac
done

[ -n "$VERSION" ] || release_die "--version is required"
[ "${#ASSETS[@]}" -gt 0 ] || release_die "At least one --asset is required"
release_validate_version "$VERSION"

REPO_ROOT="$(release_repo_root)"
TITLE="${TITLE:-$VERSION}"
FULL_GIT_HASH="${FULL_GIT_HASH:-$(release_resolve_full_hash "$REPO_ROOT")}"

for asset in "${ASSETS[@]}"; do
    [ -f "$asset" ] || release_die "Asset not found: $asset"
done

if [ -z "$NOTES_FILE" ]; then
    TEMP_NOTES_FILE="$(mktemp)"
    release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$TEMP_NOTES_FILE"
    NOTES_FILE="$TEMP_NOTES_FILE"
fi

[ -f "$NOTES_FILE" ] || release_die "Notes file not found: $NOTES_FILE"

if ! git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    echo "[create_github_release] Creating local tag $VERSION at $FULL_GIT_HASH..." >&2
    git -C "$REPO_ROOT" tag -a "$VERSION" "$FULL_GIT_HASH" -F "$NOTES_FILE"
fi

if ! git -C "$REPO_ROOT" ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1; then
    echo "[create_github_release] Pushing tag $VERSION to origin..." >&2
    git -C "$REPO_ROOT" push origin "$VERSION"
fi

if gh release view "$VERSION" >/dev/null 2>&1; then
    echo "[create_github_release] Updating existing GitHub release $VERSION..." >&2
    gh release upload "$VERSION" "${ASSETS[@]}" --clobber
    gh release edit "$VERSION" --title "$TITLE" --notes-file "$NOTES_FILE"
else
    echo "[create_github_release] Creating GitHub release $VERSION..." >&2
    gh release create "$VERSION" "${ASSETS[@]}" --title "$TITLE" --notes-file "$NOTES_FILE" --verify-tag
fi
