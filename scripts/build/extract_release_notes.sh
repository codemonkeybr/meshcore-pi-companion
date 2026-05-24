#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

usage() {
    cat <<'EOF'
Usage: scripts/build/extract_release_notes.sh --version X.Y.Z --output PATH

Options:
  --version VERSION         Release version to extract from CHANGELOG.md
  --output PATH             Output markdown file path
  --changelog PATH          Override changelog path
  --help                    Show this message
EOF
}

VERSION=""
OUTPUT_FILE=""
CHANGELOG_PATH=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="${2:-}"
            shift 2
            ;;
        --changelog)
            CHANGELOG_PATH="${2:-}"
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
[ -n "$OUTPUT_FILE" ] || release_die "--output is required"
release_validate_version "$VERSION"

REPO_ROOT="$(release_repo_root)"
release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$OUTPUT_FILE" "${CHANGELOG_PATH:-$REPO_ROOT/CHANGELOG.md}"
