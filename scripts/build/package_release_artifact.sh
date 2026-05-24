#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

usage() {
    cat <<'EOF'
Usage: scripts/build/package_release_artifact.sh --version X.Y.Z [options]

Options:
  --version VERSION         Release version (required)
  --git-hash HASH           Short git hash to embed in artifact naming
  --full-git-hash HASH      Full git hash to archive
  --output PATH             Output zip path
  --bundle-name NAME        Bundle folder name inside the zip
  --skip-prebuilt-build     Reuse existing frontend/prebuilt instead of rebuilding it
  --help                    Show this message
EOF
}

VERSION=""
GIT_HASH=""
FULL_GIT_HASH=""
OUTPUT_PATH=""
BUNDLE_NAME="Remote-Terminal-for-MeshCore"
SKIP_PREBUILT_BUILD=0

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --git-hash)
            GIT_HASH="${2:-}"
            shift 2
            ;;
        --full-git-hash)
            FULL_GIT_HASH="${2:-}"
            shift 2
            ;;
        --output)
            OUTPUT_PATH="${2:-}"
            shift 2
            ;;
        --bundle-name)
            BUNDLE_NAME="${2:-}"
            shift 2
            ;;
        --skip-prebuilt-build)
            SKIP_PREBUILT_BUILD=1
            shift
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
release_validate_version "$VERSION"

REPO_ROOT="$(release_repo_root)"
FULL_GIT_HASH="${FULL_GIT_HASH:-$(release_resolve_full_hash "$REPO_ROOT")}"
GIT_HASH="${GIT_HASH:-$(release_resolve_short_hash "$REPO_ROOT" "$FULL_GIT_HASH")}"
OUTPUT_PATH="${OUTPUT_PATH:-$REPO_ROOT/remoteterm-prebuilt-frontend-v${VERSION}-${GIT_HASH}.zip}"

WORK_DIR="$(mktemp -d)"
BUNDLE_DIR="$WORK_DIR/$BUNDLE_NAME"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

if [ "$SKIP_PREBUILT_BUILD" -eq 0 ]; then
    echo "[package_release_artifact] Building frontend prebuilt bundle..." >&2
    (
        cd "$REPO_ROOT/frontend"
        npm run packaged-build
    )
fi

[ -d "$REPO_ROOT/frontend/prebuilt" ] || release_die "frontend/prebuilt is missing; run with frontend built or omit --skip-prebuilt-build"

mkdir -p "$BUNDLE_DIR/frontend"
git -C "$REPO_ROOT" archive "$FULL_GIT_HASH" | tar -x -C "$BUNDLE_DIR"
cp -R "$REPO_ROOT/frontend/prebuilt" "$BUNDLE_DIR/frontend/prebuilt"

cat > "$BUNDLE_DIR/build_info.json" <<EOF
{
  "version": "$VERSION",
  "commit_hash": "$GIT_HASH",
  "build_source": "prebuilt-release"
}
EOF

rm -f "$OUTPUT_PATH"
(
    cd "$WORK_DIR"
    zip -qr "$OUTPUT_PATH" "$BUNDLE_NAME"
)

echo "$OUTPUT_PATH"
