"""Room polygon rendering and cache support for Botslab 360 maps."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from homeassistant.core import HomeAssistant
from PIL import Image, ImageDraw

from botslab360 import Botslab360Client, Device, Room

MAP_SIZE = 768
MAP_MARGIN = 32

_BACKGROUND = "#f4f6f8"
_OUTLINE = "#263238"
_TEXT = "#172126"
_ROOM_COLORS = (
    "#8ecae6",
    "#ffb703",
    "#90be6d",
    "#f28482",
    "#b8a1d9",
    "#84a59d",
)

Point = tuple[int, int]


@dataclass(frozen=True, slots=True)
class MapTransform:
    """Affine vendor-coordinate to image-coordinate transform."""

    min_x: int
    max_x: int
    min_y: int
    max_y: int
    scale: float
    offset_x: float
    offset_y: float

    def map_point(self, point: Point) -> tuple[float, float]:
        """Mirror X like Android and convert a vendor point to image pixels."""

        x, y = point
        return (
            self.offset_x + (self.max_x - x) * self.scale,
            self.offset_y + (y - self.min_y) * self.scale,
        )


@dataclass(frozen=True, slots=True)
class RenderedRoomMap:
    """Cached PNG and card metadata for one robot map."""

    image: bytes
    calibration_points: tuple[dict[str, dict[str, float | int]], ...]
    rooms: tuple[dict[str, Any], ...]
    predefined_selections: tuple[dict[str, Any], ...]


def polygon_centroid(vertices: tuple[Point, ...]) -> tuple[float, float]:
    """Calculate a polygon centroid, with a stable degenerate fallback."""

    twice_area = 0
    x_sum = 0
    y_sum = 0
    for current, following in zip(vertices, (*vertices[1:], vertices[0])):
        cross = current[0] * following[1] - following[0] * current[1]
        twice_area += cross
        x_sum += (current[0] + following[0]) * cross
        y_sum += (current[1] + following[1]) * cross
    if twice_area == 0:
        return (
            sum(point[0] for point in vertices) / len(vertices),
            sum(point[1] for point in vertices) / len(vertices),
        )
    return (x_sum / (3 * twice_area), y_sum / (3 * twice_area))


def map_card_selection(room: Room) -> dict[str, Any] | None:
    """Build one Xiaomi Vacuum Map Card predefined ROOM selection."""

    vertices = getattr(room, "vertices", None)
    if not vertices or len(vertices) < 3:
        return None
    centroid_x, centroid_y = polygon_centroid(vertices)
    return {
        "id": room.id,
        "outline": [list(point) for point in vertices],
        "label": {
            "text": room.name or str(room.id),
            "x": round(centroid_x, 3),
            "y": round(centroid_y, 3),
        },
    }


def _map_transform(rooms: tuple[Room, ...]) -> MapTransform | None:
    points = [
        point
        for room in rooms
        if (vertices := getattr(room, "vertices", None))
        for point in vertices
    ]
    if not points:
        return None
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x <= 0 or span_y <= 0:
        return None
    drawable = MAP_SIZE - 2 * MAP_MARGIN
    scale = min(drawable / span_x, drawable / span_y)
    rendered_width = span_x * scale
    rendered_height = span_y * scale
    return MapTransform(
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
        scale=scale,
        offset_x=(MAP_SIZE - rendered_width) / 2,
        offset_y=(MAP_SIZE - rendered_height) / 2,
    )


def _calibration_points(
    transform: MapTransform,
) -> tuple[dict[str, dict[str, float | int]], ...]:
    points = (
        (transform.min_x, transform.min_y),
        (transform.max_x, transform.min_y),
        (transform.min_x, transform.max_y),
    )
    return tuple(
        {
            "vacuum": {"x": x, "y": y},
            "map": {
                "x": round(map_x, 6),
                "y": round(map_y, 6),
            },
        }
        for (x, y) in points
        for map_x, map_y in (transform.map_point((x, y)),)
    )


def render_room_map(rooms: list[Room] | tuple[Room, ...]) -> RenderedRoomMap:
    """Render room polygons to a deterministic PNG and card metadata."""

    room_tuple = tuple(rooms)
    image = Image.new("RGB", (MAP_SIZE, MAP_SIZE), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    selections = tuple(
        selection
        for room in room_tuple
        if (selection := map_card_selection(room)) is not None
    )
    transform = _map_transform(room_tuple)
    calibration: tuple[dict[str, dict[str, float | int]], ...] = ()

    if transform is None:
        message = "Room map unavailable"
        bounds = draw.textbbox((0, 0), message)
        draw.text(
            (
                (MAP_SIZE - (bounds[2] - bounds[0])) / 2,
                (MAP_SIZE - (bounds[3] - bounds[1])) / 2,
            ),
            message,
            fill=_TEXT,
        )
    else:
        calibration = _calibration_points(transform)
        for index, room in enumerate(room_tuple):
            if not (vertices := getattr(room, "vertices", None)):
                continue
            polygon = [transform.map_point(point) for point in vertices]
            draw.polygon(
                polygon,
                fill=_ROOM_COLORS[index % len(_ROOM_COLORS)],
                outline=_OUTLINE,
                width=3,
            )
            centroid = polygon_centroid(vertices)
            label_point = transform.map_point((round(centroid[0]), round(centroid[1])))
            label = room.name or str(room.id)
            bounds = draw.textbbox((0, 0), label)
            label_width = bounds[2] - bounds[0]
            label_height = bounds[3] - bounds[1]
            draw.text(
                (
                    math.floor(label_point[0] - label_width / 2),
                    math.floor(label_point[1] - label_height / 2),
                ),
                label,
                fill=_TEXT,
            )

    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return RenderedRoomMap(
        image=output.getvalue(),
        calibration_points=calibration,
        rooms=tuple({"id": room.id, "name": room.name} for room in room_tuple),
        predefined_selections=selections,
    )


class Botslab360MapCache:
    """Fetch room geometry on demand and retain each last good rendering."""

    def __init__(self, hass: HomeAssistant, client: Botslab360Client) -> None:
        """Initialize an entry-local map cache."""

        self._hass = hass
        self._client = client
        self._maps: dict[str, RenderedRoomMap] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, device_id: str) -> RenderedRoomMap | None:
        """Return the last good map for a device."""

        return self._maps.get(device_id)

    async def async_refresh(self, device: Device) -> RenderedRoomMap:
        """Fetch and render current rooms without disturbing status polling."""

        lock = self._locks.setdefault(device.id, asyncio.Lock())
        async with lock:
            rooms = await self._client.get_rooms(device)
            rendered = await self._hass.async_add_executor_job(render_room_map, rooms)
            self._maps[device.id] = rendered
            return rendered

    def clear(self) -> None:
        """Release cached images and locks."""

        self._maps.clear()
        self._locks.clear()
