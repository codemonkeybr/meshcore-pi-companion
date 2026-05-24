#!/usr/bin/env bash

release_repo_root() {
    (
        cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
    )
}

release_die() {
    echo "Error: $*" >&2
    exit 1
}

release_trim() {
    printf '%s' "$1" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

release_validate_version() {
    local version="$1"
    [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || release_die "Version must be in format X.Y.Z"
}

release_resolve_full_hash() {
    local repo_root="$1"
    local ref="${2:-HEAD}"
    git -C "$repo_root" rev-parse "$ref"
}

release_resolve_short_hash() {
    local repo_root="$1"
    local ref="${2:-HEAD}"
    git -C "$repo_root" rev-parse --short "$ref"
}

release_format_markdown_list() {
    local input_file="$1"
    local output_file="$2"
    awk '
        /^[[:space:]]*$/ { next }
        {
            sub(/^[[:space:]]+/, "", $0)
            if ($0 ~ /^\* /) {
                print
            } else if ($0 ~ /^- /) {
                sub(/^- /, "* ", $0)
                print
            } else {
                print "* " $0
            }
        }
    ' "$input_file" > "$output_file"
}

release_extract_changelog_section() {
    local repo_root="$1"
    local version="$2"
    local output_file="$3"
    local changelog_path="${4:-$repo_root/CHANGELOG.md}"

    # Use index() for literal matching so dots in version strings are not
    # treated as regex wildcards (e.g. 3.6.5 won't match 31615).
    awk -v ver="$version" '
        BEGIN { header = "## [" ver "]" }
        index($0, header) == 1 { capture = 1; print; next }
        capture && /^## \[/ { exit }
        capture { print }
    ' "$changelog_path" > "$output_file"

    [ -s "$output_file" ] || release_die "Could not find CHANGELOG entry for version $version"
}

release_ensure_buildx_builder() {
    if ! docker buildx version >/dev/null 2>&1; then
        release_die "docker buildx is required for multi-arch Docker builds"
    fi

    # Multi-platform builds require the docker-container driver. The default
    # builder uses the "docker" driver which only supports the host platform.
    # Check the current builder's driver first; only create a new one if needed.
    local current_driver
    current_driver="$(docker buildx inspect --format '{{ .Driver }}' 2>/dev/null || true)"
    if [ "$current_driver" = "docker-container" ]; then
        docker buildx inspect --bootstrap >/dev/null
        return
    fi

    if docker buildx inspect remoteterm-multiarch >/dev/null 2>&1; then
        docker buildx use remoteterm-multiarch >/dev/null
    else
        docker buildx create --name remoteterm-multiarch --use >/dev/null
    fi
    docker buildx inspect --bootstrap >/dev/null
}
