"""Tests for room-to-area matching."""

from dataclasses import replace
from types import SimpleNamespace

from custom_components.botslab360.areas import (
    DiscoveredRoom,
    automatic_room_area_matches,
    normalize_area_name,
    room_area_fields,
)

from .conftest import TEST_DEVICE, TEST_ROOMS


def _area(area_id: str, name: str):
    return SimpleNamespace(id=area_id, name=name)


def test_area_name_normalization() -> None:
    """Test case and repeated surrounding whitespace are normalized."""

    assert normalize_area_name("  Wohn   Zimmer  ") == "wohn zimmer"
    assert normalize_area_name("KÜCHE") == normalize_area_name("küche")


def test_exact_normalized_area_matches_are_automatic() -> None:
    """Test exact, case-insensitive, whitespace-normalized matching."""

    rooms = [DiscoveredRoom(TEST_DEVICE, TEST_ROOMS[0])]

    assert automatic_room_area_matches(rooms, [_area("bath", "  bAd ")]) == {
        f"{TEST_DEVICE.id}:1": "bath"
    }


def test_non_matching_area_is_not_guessed() -> None:
    """Test unrelated names never receive a fuzzy match."""

    rooms = [DiscoveredRoom(TEST_DEVICE, TEST_ROOMS[0])]

    assert automatic_room_area_matches(rooms, [_area("office", "Bathroom")]) == {}


def test_ambiguous_normalized_area_is_not_guessed() -> None:
    """Test duplicate normalized Area names are left unassigned."""

    rooms = [DiscoveredRoom(TEST_DEVICE, TEST_ROOMS[0])]
    areas = [_area("bath-1", "Bad"), _area("bath-2", " bad ")]

    assert automatic_room_area_matches(rooms, areas) == {}


def test_explicit_mapping_survives_room_rename() -> None:
    """Test a user mapping wins even after a vendor room-name change."""

    renamed = replace(TEST_ROOMS[0], name="Bathroom")
    room = DiscoveredRoom(TEST_DEVICE, renamed)
    fields = room_area_fields(
        [room],
        [_area("bath", "Bad"), _area("other", "Bathroom")],
        {room.mapping_key: "bath"},
    )

    assert fields[0].area_id == "bath"


def test_explicit_unassigned_wins_over_automatic_match() -> None:
    """Test clearing a mapping prevents it from being auto-selected again."""

    room = DiscoveredRoom(TEST_DEVICE, TEST_ROOMS[0])
    fields = room_area_fields(
        [room],
        [_area("bath", "Bad")],
        {room.mapping_key: None},
    )

    assert fields[0].area_id is None
