#!/usr/bin/env bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo -e "${YELLOW}=== RemoteTerm for MeshCore Publish Script ===${NC}"
echo

# Run backend linting and type checking
echo -e "${YELLOW}Running backend lint (Ruff)...${NC}"
uv run ruff check app/ tests/ --fix
uv run ruff format app/ tests/
# validate
uv run ruff check app/ tests/
uv run ruff format --check app/ tests/
echo -e "${GREEN}Backend lint passed!${NC}"
echo

echo -e "${YELLOW}Running backend type check (Pyright)...${NC}"
uv run pyright app/
echo -e "${GREEN}Backend type check passed!${NC}"
echo

# Run backend tests
echo -e "${YELLOW}Running backend tests...${NC}"
PYTHONPATH=. uv run pytest tests/ -v
echo -e "${GREEN}Backend tests passed!${NC}"
echo

# Run frontend linting and formatting check
echo -e "${YELLOW}Running frontend lint (ESLint)...${NC}"
cd "$SCRIPT_DIR/frontend"
npm run lint
echo -e "${GREEN}Frontend lint passed!${NC}"
echo

echo -e "${YELLOW}Checking frontend formatting (Prettier)...${NC}"
npm run format:check
echo -e "${GREEN}Frontend formatting OK!${NC}"
echo

# Run frontend tests and build
echo -e "${YELLOW}Running frontend tests...${NC}"
npm run test:run
echo -e "${GREEN}Frontend tests passed!${NC}"
echo

echo -e "${YELLOW}Building frontend...${NC}"
npm run build
echo -e "${GREEN}Frontend build complete!${NC}"
cd "$SCRIPT_DIR"
echo

echo -e "${YELLOW}Regenerating LICENSES.md...${NC}"
bash scripts/collect_licenses.sh LICENSES.md
echo -e "${GREEN}LICENSES.md updated!${NC}"
echo

# Prompt for version
echo -e "${YELLOW}Current versions:${NC}"
echo -n "  pyproject.toml: "
grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
echo -n "  package.json:   "
grep '"version"' frontend/package.json | head -1 | sed 's/.*"version": "\(.*\)".*/\1/'
echo

read -r -p "Enter new version (e.g., 1.2.3): " VERSION
VERSION="$(printf '%s' "$VERSION" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

if [[ ! $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Version must be in format X.Y.Z${NC}"
    exit 1
fi

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
echo -e "${YELLOW}Enter changelog entry for version $VERSION${NC}"
echo -e "${YELLOW}(Enter your changes, then press Ctrl+D when done):${NC}"
echo

CHANGELOG_ENTRY=$(cat)

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
            echo "$CHANGELOG_ENTRY"
            echo
            tail -n +2 CHANGELOG.md
        } > CHANGELOG.md.tmp
        mv CHANGELOG.md.tmp CHANGELOG.md
    else
        # No title, prepend directly
        {
            echo "$CHANGELOG_HEADER"
            echo
            echo "$CHANGELOG_ENTRY"
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
        echo "$CHANGELOG_ENTRY"
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

# Build docker image
echo -e "${YELLOW}Building Docker image...${NC}"
docker build --build-arg COMMIT_HASH=$GIT_HASH \
             -t jkingsman/remoteterm-meshcore:latest \
             -t jkingsman/remoteterm-meshcore:$VERSION \
             -t jkingsman/remoteterm-meshcore:$GIT_HASH .
echo -e "${GREEN}Docker build complete!${NC}"
echo

# Push docker images
echo -e "${YELLOW}Pushing Docker images...${NC}"
docker push jkingsman/remoteterm-meshcore:latest
docker push jkingsman/remoteterm-meshcore:$VERSION
docker push jkingsman/remoteterm-meshcore:$GIT_HASH
echo -e "${GREEN}Docker push complete!${NC}"
echo

# Create GitHub release using the changelog notes for this version.
echo -e "${YELLOW}Creating GitHub release...${NC}"
RELEASE_NOTES_FILE=$(mktemp)
{
    echo "$CHANGELOG_HEADER"
    echo
    echo "$CHANGELOG_ENTRY"
} > "$RELEASE_NOTES_FILE"

# Create and push the release tag first so GitHub release creation does not
# depend on resolving a symbolic ref like HEAD on the remote side. Use the same
# changelog-derived notes for the annotated tag message.
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    echo -e "${YELLOW}Tag $VERSION already exists locally; reusing it.${NC}"
else
    git tag -a "$VERSION" "$FULL_GIT_HASH" -F "$RELEASE_NOTES_FILE"
fi

if git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1; then
    echo -e "${YELLOW}Tag $VERSION already exists on origin; not pushing it again.${NC}"
else
    git push origin "$VERSION"
fi

gh release create "$VERSION" \
    --title "$VERSION" \
    --notes-file "$RELEASE_NOTES_FILE" \
    --verify-tag

rm -f "$RELEASE_NOTES_FILE"
echo -e "${GREEN}GitHub release created!${NC}"
echo

echo -e "${GREEN}=== Publish complete! ===${NC}"
echo -e "Version: ${YELLOW}$VERSION${NC}"
echo -e "Git hash: ${YELLOW}$GIT_HASH${NC}"
echo -e "Docker tags pushed:"
echo -e "  - jkingsman/remoteterm-meshcore:latest"
echo -e "  - jkingsman/remoteterm-meshcore:$VERSION"
echo -e "  - jkingsman/remoteterm-meshcore:$GIT_HASH"
echo -e "GitHub release:"
echo -e "  - $VERSION"
