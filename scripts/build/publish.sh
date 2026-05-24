#!/usr/bin/env bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

DOCKER_IMAGE="docker.io/jkingsman/remoteterm-meshcore"
DOCKER_PLATFORMS="linux/amd64,linux/arm64"
VERSION=""
NOTES_FILE=""
SKIP_QUALITY=0
RELEASE_ASSET_PATH=""

usage() {
    cat <<'EOF'
Usage: scripts/build/publish.sh [options]

Options:
  --version VERSION         Release version; prompts if omitted
  --notes-file PATH         File containing changelog entry lines; prompts if omitted
  --skip-quality            Skip ./scripts/quality/all_quality.sh
  --help                    Show this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --notes-file)
            NOTES_FILE="${2:-}"
            shift 2
            ;;
        --skip-quality)
            SKIP_QUALITY=1
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

echo -e "${YELLOW}=== RemoteTerm for MeshCore Publish Script ===${NC}"
echo

if [ "$SKIP_QUALITY" -eq 0 ]; then
    echo -e "${YELLOW}Running repo quality gate...${NC}"
    ./scripts/quality/all_quality.sh
    echo -e "${GREEN}Quality gate passed!${NC}"
    echo
fi

echo -e "${YELLOW}Regenerating LICENSES.md...${NC}"
bash scripts/build/collect_licenses.sh LICENSES.md
echo -e "${GREEN}LICENSES.md updated!${NC}"
echo

# Prompt for version
echo -e "${YELLOW}Current versions:${NC}"
echo -n "  pyproject.toml: "
grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
echo -n "  package.json:   "
grep '"version"' frontend/package.json | head -1 | sed 's/.*"version": "\(.*\)".*/\1/'
echo

if [ -z "$VERSION" ]; then
    read -r -p "Enter new version (e.g., 1.2.3): " VERSION
fi
VERSION="$(release_trim "$VERSION")"
release_validate_version "$VERSION"

# Update pyproject.toml
echo -e "${YELLOW}Updating pyproject.toml...${NC}"
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

# Update frontend package.json
echo -e "${YELLOW}Updating frontend/package.json...${NC}"
sed -i "s/\"version\": \".*\"/\"version\": \"$VERSION\"/" frontend/package.json

# Update uv.lock with new version
echo -e "${YELLOW}Updating uv.lock...${NC}"
uv sync

echo -e "${GREEN}Version updated to $VERSION${NC}"
echo

# Prompt for changelog entry
RAW_CHANGELOG_INPUT_FILE="$(mktemp)"
FORMATTED_CHANGELOG_INPUT_FILE="$(mktemp)"
cleanup() {
    rm -f "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE"
    rm -rf "${REPO_ROOT:?}/frontend/prebuilt"
    if [ -n "$RELEASE_ASSET_PATH" ] && [ -f "$RELEASE_ASSET_PATH" ]; then
        rm -f "$RELEASE_ASSET_PATH"
    fi
}
trap cleanup EXIT

if [ -n "$NOTES_FILE" ]; then
    cp "$NOTES_FILE" "$RAW_CHANGELOG_INPUT_FILE"
else
    echo -e "${YELLOW}Enter changelog entry for version $VERSION${NC}"
    echo -e "${YELLOW}(Enter your changes, then press Ctrl+D when done):${NC}"
    echo
    cat > "$RAW_CHANGELOG_INPUT_FILE"
fi

release_format_markdown_list "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE"
[ -s "$FORMATTED_CHANGELOG_INPUT_FILE" ] || release_die "Changelog entry cannot be empty"

# Create changelog entry with date
DATE=$(date +%Y-%m-%d)
CHANGELOG_HEADER="## [$VERSION] - $DATE"

# Prepend to CHANGELOG.md (after the title if it exists)
if [ -f CHANGELOG.md ]; then
    # Check if file starts with a title
    if head -1 CHANGELOG.md | grep -q "^# "; then
        # Insert after title line
        {
            head -1 CHANGELOG.md
            echo
            echo "$CHANGELOG_HEADER"
            echo
            cat "$FORMATTED_CHANGELOG_INPUT_FILE"
            echo
            tail -n +2 CHANGELOG.md
        } > CHANGELOG.md.tmp
        mv CHANGELOG.md.tmp CHANGELOG.md
    else
        # No title, prepend directly
        {
            echo "$CHANGELOG_HEADER"
            echo
            cat "$FORMATTED_CHANGELOG_INPUT_FILE"
            echo
            cat CHANGELOG.md
        } > CHANGELOG.md.tmp
        mv CHANGELOG.md.tmp CHANGELOG.md
    fi
else
    # Create new changelog
    {
        echo "# Changelog"
        echo
        echo "$CHANGELOG_HEADER"
        echo
        cat "$FORMATTED_CHANGELOG_INPUT_FILE"
    } > CHANGELOG.md
fi

echo
echo -e "${GREEN}Changelog updated!${NC}"
echo

# Commit the changes
echo -e "${YELLOW}Committing changes...${NC}"
git add .
git commit -m "Updating changelog + build for $VERSION"
git push
echo -e "${GREEN}Changes committed!${NC}"
echo

# Get git hashes (after commit so they reflect the new commit)
GIT_HASH=$(git rev-parse --short HEAD)
FULL_GIT_HASH=$(git rev-parse HEAD)
RELEASE_ASSET="remoteterm-prebuilt-frontend-v${VERSION}-${GIT_HASH}.zip"
RELEASE_ASSET_PATH="$REPO_ROOT/$RELEASE_ASSET"

echo -e "${YELLOW}Building packaged frontend artifact...${NC}"
scripts/build/package_release_artifact.sh \
    --version "$VERSION" \
    --git-hash "$GIT_HASH" \
    --full-git-hash "$FULL_GIT_HASH" \
    --output "$RELEASE_ASSET_PATH"
echo -e "${GREEN}Packaged release artifact created: $RELEASE_ASSET${NC}"
echo

# Build and push multi-arch docker image
echo -e "${YELLOW}Building and pushing multi-arch Docker image...${NC}"
scripts/build/push_docker_multiarch.sh \
    --version "$VERSION" \
    --git-hash "$GIT_HASH" \
    --image "$DOCKER_IMAGE" \
    --platforms "$DOCKER_PLATFORMS"
echo -e "${GREEN}Multi-arch Docker build + push complete!${NC}"
echo

# Create GitHub release using the changelog notes for this version.
echo -e "${YELLOW}Creating GitHub release...${NC}"
scripts/build/create_github_release.sh \
    --version "$VERSION" \
    --full-git-hash "$FULL_GIT_HASH" \
    --asset "$RELEASE_ASSET_PATH"
echo -e "${GREEN}GitHub release created!${NC}"
echo

echo -e "${GREEN}=== Publish complete! ===${NC}"
echo -e "Version: ${YELLOW}$VERSION${NC}"
echo -e "Git hash: ${YELLOW}$GIT_HASH${NC}"
echo -e "Docker tags pushed:"
echo -e "  - $DOCKER_IMAGE:latest"
echo -e "  - $DOCKER_IMAGE:$VERSION"
echo -e "  - $DOCKER_IMAGE:$GIT_HASH"
echo -e "Platforms:"
echo -e "  - linux/amd64"
echo -e "  - linux/arm64"
echo -e "GitHub release:"
echo -e "  - $VERSION"
echo -e "Release artifact:"
echo -e "  - $RELEASE_ASSET"
