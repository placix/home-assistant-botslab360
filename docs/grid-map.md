# Occupancy-grid map (robots without room segmentation)

Robots such as the **360 S6** have no room segmentation. Their `MapInfo` has an
integer `mapId` but **no `cleanId`/`smartArea`**, and `area` is `[]`. Since
0.7.4, `get_rooms()` returns `[]` for them (issue #5).

The same `MapInfo` still contains a full **occupancy grid**: the floor, the walls
and the dock, i.e. the map the vendor app draws. This document describes that
format and how this integration turns it into a camera entity.

## Where the data comes from

The request is the same as for room discovery: `infoType 30000` (`COMPOSITE_INFO_TYPE`)
with `LOAD_DATA`, sent with `_post_robot_request`. The answer arrives on the push
channel and is resolved with `_map_from_protocol` (including `pushDataUrl`
downloads). `fetch_map_info()` in `grid_map.py` repeats
`Botslab360Client._get_room_map_once` up to the point where it returns the raw
`MapInfo`, without calling `parse_room_map`.

## `MapInfo` fields used (real S6)

| Field | Example | Meaning |
|---|---|---|
| `mapId` | 1529464470 | Integer map id |
| `width` × `height` | 166 × 92 | Grid size in cells |
| `resolution` | 0.05 | Metres per cell |
| `x_min`, `y_min` | -7.68, -3.03 | Origin in metres (cell 0,0) |
| `map` | base64 text | One **raw LZ4 block** (no frame header). `+` arrives as a space: replace `' '` with `'+'` before decoding |
| `lz4_len` | 1107 | Size of the compressed block (used as a check) |
| `chargeHandlePos` | [-6965, 273] | Dock position in **mm** |
| `area` | `[]` | Empty, because there are no rooms |

The decompressed grid is `width × height` bytes, and row 0 is `y_min`:

| Value | Meaning |
|---|---|
| 127 | Unknown |
| 255 | Floor |
| 0 | Wall / obstacle |

The robot position comes from the normal status (`position_x`, `position_y`),
in mm and in the same frame as the dock.

## Rendering

- **Orientation:** X is mirrored and image-y grows with world-y. This is the same
  transform as `map.MapTransform.map_point` for the room map, and it matches the
  360 app (checked on a real S6).
- **Pixel position:**
  - `col = width − (x/1000 − x_min)/resolution`
  - `row = (y/1000 − y_min)/resolution`
  - both multiplied by an integer scale, so the longest side is about 1024 px
    (at most 6×).
- **Markers:** the dock is green and the robot red. The base image is cached;
  only the markers are redrawn when the robot moves.

## Camera behaviour

- Only robots whose `get_rooms()` returned `[]` at setup get a camera
  (`runtime_data.unsegmented_devices`). Robots with rooms get no camera entity,
  so the deferred polygon camera stays deferred.
- Known limitation: in 0.7.4 `get_rooms()` also returns `[]` for a room-capable
  robot whose `smartArea` is still empty (no rooms created yet). That robot would
  get a grid camera too, until it has rooms and the entry reloads. A public
  library flag (e.g. `supports_rooms`/`segmented`) would make this exact.
- `async_camera_image()` returns the cached PNG immediately and refreshes the grid
  in a background task tied to the config entry. The grid is refreshed after
  60 s while the robot is active and after 15 min when it is idle. After a
  failure it backs off for 5 min. The first request waits up to 8 s, because
  HA's camera proxy allows about 10 s.
- Error handling: `AuthenticationError` starts reauthentication. `ApiError`,
  timeouts and decode errors are logged and keep the last good grid. A rendering
  error returns no image for that request.
- **Limits:** the LZ4 decoder is bounds-checked and never grows past
  `width × height`. The grid is limited to 1 M cells.

## Proposed library / integration split

This PR keeps everything in the integration so that it can be tested end to end
on a real S6. To follow `AGENTS.md`, the protocol part should move to
`python-botslab360`:

| python-botslab360 (proposed public API) | Home Assistant integration |
|---|---|
| `await client.get_map_info(device) -> dict` (raw `MapInfo`, same transport as `get_rooms`) | Camera entity, refresh policy, caching |
| `decode_occupancy_grid(map_info) -> OccupancyGrid` (width, height, resolution, origin, cells, dock) including the LZ4 block decoder and validation | PNG rendering (Pillow) and orientation |

With that split, `fetch_map_info()` and `_lz4_block()`/`decode_grid_map()`
in `grid_map.py` become thin calls to the library, and the integration no longer
uses private client attributes.
