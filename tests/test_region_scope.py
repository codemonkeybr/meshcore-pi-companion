from app.region_scope import (
    UNSCOPED_OVERRIDE_MARKER,
    is_unscoped,
    normalize_region_scope,
)


def test_normalize_region_scope_preserves_regions():
    assert normalize_region_scope("Esperance") == "#Esperance"
    assert normalize_region_scope("#Esperance") == "#Esperance"


def test_normalize_region_scope_unscoped_sentinels():
    assert normalize_region_scope(None) == ""
    assert normalize_region_scope("") == ""
    assert normalize_region_scope("   ") == ""
    assert normalize_region_scope("0") == ""
    assert normalize_region_scope("*") == ""


def test_is_unscoped():
    assert is_unscoped(None) is True
    assert is_unscoped("") is True
    assert is_unscoped("   ") is True
    assert is_unscoped("0") is True
    assert is_unscoped("*") is True
    assert is_unscoped(UNSCOPED_OVERRIDE_MARKER) is True
    assert is_unscoped("Esperance") is False
    assert is_unscoped("#Esperance") is False


def test_unscoped_override_marker_is_recognized_and_not_a_region():
    # The canonical persisted marker must round-trip as unscoped, never as a region.
    assert is_unscoped(UNSCOPED_OVERRIDE_MARKER) is True
    assert normalize_region_scope(UNSCOPED_OVERRIDE_MARKER) == ""
