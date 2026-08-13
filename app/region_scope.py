"""Helpers for normalizing MeshCore flood-scope / region names."""

# Canonical persisted marker for "force unscoped/plain flood". Stored verbatim in
# the per-channel ``flood_scope_override`` column to mean "this channel is unscoped
# even if a global region is set" — distinct from NULL, which means "inherit global".
UNSCOPED_OVERRIDE_MARKER = "*"

# All values that denote explicit unscoped/plain flood, matching firmware parity.
_UNSCOPED_SENTINELS = {"", "0", UNSCOPED_OVERRIDE_MARKER}


def is_unscoped(scope: str | None) -> bool:
    """True if ``scope`` denotes an explicit unscoped/plain-flood request.

    Note: an empty string counts as unscoped here. Callers that need to treat
    blank as "no opinion / inherit" (e.g. the channel-override API) must check for
    blank *before* calling this.
    """
    return (scope or "").strip() in _UNSCOPED_SENTINELS


def normalize_region_scope(scope: str | None) -> str:
    """Normalize a user-facing region scope into MeshCore's internal form.

    Region names are user-facing plain strings like ``Esperance``. Internally,
    MeshCore still expects hashtag-style names like ``#Esperance``.

    Backward compatibility / firmware parity:
    - blank/None stays unscoped (``""``)
    - ``"0"`` and ``"*"`` also mean explicit unscoped/plain flood
    - existing leading ``#`` is preserved
    """

    stripped = (scope or "").strip()
    if stripped in _UNSCOPED_SENTINELS:
        return ""
    if stripped.startswith("#"):
        return stripped
    return f"#{stripped}"
