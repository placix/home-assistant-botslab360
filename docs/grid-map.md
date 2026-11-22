# Occupancy-grid map camera

Robots whose current map carries an occupancy grid (for example the **360 S6**,
which has no room segmentation) get a **Map** camera. It shows the floor, the
walls, the dock and the live robot position, as in the vendor app.

## Where the data comes from

The grid comes from the public library API (`botslab360==0.7.5`):

```python
grid = await client.get_occupancy_grid(device, timeout=30.0)
```

The library owns the MapInfo transport, the Base64 and LZ4 decoding and the
vendor validation. It returns a typed `OccupancyGrid`:

| Field | Meaning |
|---|---|
| `map_id` | Integer map id |
| `width` × `height` | Grid size in cells |
| `resolution` | Metres per cell (S6) |
| `x_min`, `y_min` | Origin in metres (S6); row 0 is at `y_min` |
| `cells` | `width × height` bytes, row-major |
| `dock_position` | Dock in mm, same frame as the status position, or `None` |

Cell values: `GRID_CELL_FREE` (255), `GRID_CELL_OCCUPIED` (0) and
`GRID_CELL_UNKNOWN` (127), as observed on the S6. Other values are drawn as
unknown.

## Capability handling

Whether a robot gets a camera depends only on `get_occupancy_grid()`. Room
discovery (`get_rooms()`) is not used for this.

| Result | Behaviour |
|---|---|
| Success | A camera is added for the robot |
| `UnsupportedOccupancyGridError` before any success | No grid camera for this config-entry lifetime |
| `UnsupportedOccupancyGridError` after a success | The camera keeps its last image; treated as a temporary failure |
| `AuthenticationError` | Reauthentication starts; no further grid requests for that robot (not even forced camera refreshes) until reauthentication reloads the entry |
| Temporary `ApiError`, timeout, transport or unexpected error | Retry with backoff (5 min, doubling up to 1 h); never marked unsupported |

Discovery runs in a background task bound to the config entry, so optional map
support never delays the vacuum and sensor setup. At most one fetch runs per
robot. Unloading the entry cancels pending fetches and retry timers, and a fetch
that finishes during unloading adds no entity.

## Rendering

- **Orientation:** X is mirrored and image-y grows with world-y. This is the same
  transform as `map.MapTransform.map_point` for the room map, and it matches the
  360 app (checked on a real S6).
- **Pixel position:**
  - `col = width − (x/1000 − x_min)/resolution`
  - `row = (y/1000 − y_min)/resolution`
  - both multiplied by an integer scale, so the longest side is about 1024 px
    (at most 6×). A grid longer than 1024 cells is scaled down instead, so the
    longest side is exactly 1024 px; the markers use the per-axis factors of
    the resized image.
- **Markers:** the dock is green and the robot red. A status position of
  `(0, 0)` means "no position yet" and is not drawn. The base image is cached;
  only the markers are redrawn when the robot moves, and the PNG is cached per
  grid and robot position.

## Camera behaviour

- Unique ID: `{device_id}_occupancy_grid_map`, so it never collides with the
  retained polygon room-map camera (`{device_id}_map`), which stays deferred.
- The camera reports `image/png` (`content_type` is set after
  `Camera.__init__`, which would otherwise reset it to JPEG).
- `async_camera_image()` returns the cached PNG immediately and refreshes the
  grid in the background: after 60 s while the robot is active and after 15 min
  when it is idle. A failed refresh keeps the last image and backs off. When no
  grid is cached yet, the first request waits up to 8 s, because HA's camera
  proxy allows about 10 s.
