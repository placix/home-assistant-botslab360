"""Occupancy-grid map for robots without room segmentation (e.g. 360 S6).

Reference implementation for discussion: the MapInfo request and the LZ4 grid
decoder belong in python-botslab360 (see docs/grid-map.md for the proposed split).

The S6 MapInfo has no cleanId/smartArea (no rooms) but carries an LZ4 grid:
width x height cells of `resolution` metres, origin (x_min, y_min) in metres,
grid row 0 = y_min, cell values 127 = unknown, 255 = floor, 0 = wall/obstacle.
Robot and dock positions are in millimetres in the same frame.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import time
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw

from botslab360 import ApiError, AuthenticationError
from botslab360 import rooms as _rooms

# Orientation. Upstream map.MapTransform.map_point (rooms) mirrors X and keeps
# image-y growing with world-y, i.e. a 180 degree rotation of a north-up view.
# Use the same so both renderers agree (matches the 360 app, checked on a real S6).
MIRROR_X = True
FLIP_Y = False  # False = grid row 0 (y_min) at the top of the image

MAX_SIDE_PX = 1024
MAX_SCALE = 6
MAX_CELLS = 1_000_000

_UNKNOWN = (40, 44, 52)
_FLOOR = (120, 170, 220)
_WALL = (240, 240, 240)
_DOCK = (60, 200, 90)
_ROBOT = (230, 60, 60)


@dataclass(slots=True)
class GridMap:
    """Decoded grid geometry and the rendered base image."""

    map_id: int
    width: int
    height: int
    resolution: float
    x_min: float
    y_min: float
    scale: int
    dock: tuple[int, int] | None
    base: Image.Image
    fetched: float = field(default_factory=time.monotonic)


def _lz4_block(src: bytes, out_len: int) -> bytes:
    """Decode one raw LZ4 block, refusing to grow past out_len."""

    dst = bytearray()
    i = 0
    n = len(src)
    try:
        while i < n:
            token = src[i]
            i += 1
            lit = token >> 4
            if lit == 15:
                while True:
                    b = src[i]
                    i += 1
                    lit += b
                    if b != 255:
                        break
            if i + lit > n or len(dst) + lit > out_len:
                raise ApiError("Corrupt LZ4 map (literals)", phase="protocol")
            dst += src[i : i + lit]
            i += lit
            if i >= n:
                break
            offset = src[i] | (src[i + 1] << 8)
            i += 2
            if offset == 0 or offset > len(dst):
                raise ApiError("Corrupt LZ4 map (offset)", phase="protocol")
            mlen = token & 15
            if mlen == 15:
                while True:
                    b = src[i]
                    i += 1
                    mlen += b
                    if b != 255:
                        break
            mlen += 4
            if len(dst) + mlen > out_len:
                raise ApiError("Corrupt LZ4 map (match)", phase="protocol")
            for _ in range(mlen):
                dst.append(dst[-offset])
    except IndexError as err:
        raise ApiError("Truncated LZ4 map", phase="protocol") from err
    if len(dst) != out_len:
        raise ApiError(
            f"Map grid size mismatch ({len(dst)} != {out_len})", phase="protocol"
        )
    return bytes(dst)


def decode_grid_map(info: dict[str, Any]) -> GridMap:
    """Decode a raw MapInfo dict into a GridMap (raises ApiError on bad data)."""

    try:
        width = int(info["width"])
        height = int(info["height"])
        resolution = float(info["resolution"])
        x_min = float(info["x_min"])
        y_min = float(info["y_min"])
        encoded = str(info["map"]).replace(" ", "+")
        if width <= 0 or height <= 0 or width * height > MAX_CELLS:
            raise ValueError(f"invalid grid size {width}x{height}")
        if not (math.isfinite(resolution) and resolution > 0):
            raise ValueError(f"invalid resolution {resolution}")
        if not (math.isfinite(x_min) and math.isfinite(y_min)):
            raise ValueError("invalid origin")
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
        lz4_len = info.get("lz4_len")
        if isinstance(lz4_len, int) and not isinstance(lz4_len, bool) and lz4_len != len(raw):
            raise ValueError(f"lz4_len {lz4_len} != {len(raw)}")
        grid = _lz4_block(raw, width * height)

        dock = info.get("chargeHandlePos")
        dock_xy = None
        if isinstance(dock, list) and len(dock) == 2:
            dock_xy = (int(dock[0]), int(dock[1]))

        img = Image.frombytes("P", (width, height), grid)  # row 0 = y_min
        palette = list(_UNKNOWN) * 256
        palette[0:3] = _WALL
        palette[255 * 3 : 256 * 3] = _FLOOR
        img.putpalette(palette)
        img = img.convert("RGB")
        if MIRROR_X:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if FLIP_Y:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        scale = max(1, min(MAX_SCALE, MAX_SIDE_PX // max(width, height)))
        base = img.resize((width * scale, height * scale), Image.Resampling.NEAREST)
        map_id = int(info.get("mapId") or 0)
    except ApiError:
        raise
    except (KeyError, TypeError, ValueError, binascii.Error, OSError) as err:
        raise ApiError(f"MapInfo has no usable grid map: {err!r}", phase="protocol") from err

    return GridMap(
        map_id=map_id,
        width=width,
        height=height,
        resolution=resolution,
        x_min=x_min,
        y_min=y_min,
        scale=scale,
        dock=dock_xy,
        base=base,
    )


def _to_pixel(grid: GridMap, x_mm: float, y_mm: float) -> tuple[float, float]:
    col = (x_mm / 1000 - grid.x_min) / grid.resolution
    row = (y_mm / 1000 - grid.y_min) / grid.resolution  # row 0 = y_min
    if MIRROR_X:
        col = grid.width - col
    if FLIP_Y:
        row = grid.height - row
    return col * grid.scale, row * grid.scale


def render_grid_png(grid: GridMap, robot: tuple[int, int] | None) -> bytes:
    """Draw dock and robot on top of the cached base image."""

    img = grid.base.copy()
    draw = ImageDraw.Draw(img)
    radius_dock = max(4, int(1.7 * grid.scale))
    radius_robot = max(3, int(1.4 * grid.scale))
    for point, color, radius in (
        (grid.dock, _DOCK, radius_dock),
        (robot, _ROBOT, radius_robot),
    ):
        if point is None:
            continue
        cx, cy = _to_pixel(grid, *point)
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=color,
            outline=(255, 255, 255),
            width=2,
        )
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def fetch_map_info(client: Any, device_id: str, timeout: float = 30.0) -> dict:
    """Request MapInfo and return the raw payload (no room parsing).

    Mirrors Botslab360Client._get_room_map_once (library 0.7.4) but stops before
    parse_room_map. TEMPORARY: uses private library internals; to be replaced by a
    public library call (proposed: client.get_map_info(device)).
    """

    async def once() -> dict:
        if client._session is None:  # noqa: SLF001
            raise AuthenticationError(
                "Authentication is required before requesting the map",
                phase="authentication",
            )
        async with client._push_connection() as push:  # noqa: SLF001

            async def wait() -> dict:
                while True:
                    event = await push.read_event()
                    if event.get("sn") != device_id:
                        continue
                    try:
                        protocol = _rooms._json_object(  # noqa: SLF001
                            event.get("data"), field="protocol"
                        )
                    except ApiError:
                        continue
                    info = await _rooms._map_from_protocol(  # noqa: SLF001
                        protocol,
                        http_client=client._http_client,  # noqa: SLF001
                        request_timeout=client._request_timeout,  # noqa: SLF001
                    )
                    if info is not None:
                        return _rooms._json_object(info, field="MapInfo")  # noqa: SLF001

            waiter = asyncio.create_task(asyncio.wait_for(wait(), timeout=timeout))
            await asyncio.sleep(0)
            try:
                await client._post_robot_request(  # noqa: SLF001
                    device_id=device_id,
                    info_type=_rooms.COMPOSITE_INFO_TYPE,
                    data=json.dumps(_rooms.LOAD_DATA, separators=(",", ":")),
                    task_id=str(uuid4()),
                    operation="grid map request",
                )
                try:
                    return await waiter
                except asyncio.TimeoutError as err:
                    raise ApiError("Timed out waiting for map", phase="timeout") from err
            finally:
                if not waiter.done():
                    waiter.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await waiter

    return await client._with_session_refresh(once)  # noqa: SLF001
