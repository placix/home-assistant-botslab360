# Entity inventory

Botslab 360 keeps everyday controls visible and registers technical diagnostics
disabled by default. Missing or unrecognized source values remain unknown; they
are not inferred from unrelated protocol fields.

## Physical robot device

| Entity | Platform | Source | Default | Category | Semantics and unknown behavior |
| --- | --- | --- | --- | --- | --- |
| Robot | Vacuum | `RobotStatus.state`, `error_code` | Enabled | None | HA vacuum activity; unknown vendor states remain unknown. |
| Battery | Sensor | `RobotStatus.battery` | Enabled | None | Current battery percentage. |
| Cleaned area | Sensor | `RobotStatus.cleaned_area_m2` | Enabled | None | Current run area in m². |
| Cleaning time | Sensor | `RobotStatus.cleaning_time_seconds` | Enabled | None | Current run duration in seconds. |
| Error code | Sensor | `RobotStatus.error_code` | Enabled | Diagnostic | Vendor error code; zero means no reported error. |
| Cleaning mode | Select | `mop_status`, `set_mop_only()` | Enabled | None | Hardware-aware sweep/sweep-and-mop/mop control; installed hardware with unreadable mop-only state remains unknown after restart. |
| Wiping assembly | Binary sensor | `RobotStatus.mop_status` | Enabled | None | `0` absent, `1` present; all other or missing values are unknown. |
| Start cleaning job | Button | HA room-job state, `clean_rooms()` | Enabled | None | Sends all currently selected room IDs. |
| Clear room selection | Button | HA room-job state | Enabled | None | Clears the temporary selection without robot I/O. |
| Fan mode | Sensor | `RobotStatus.fan_mode` | Disabled | Diagnostic | Raw current vendor fan mode. |
| Raw robot state | Sensor | `RobotStatus.state` | Disabled | Diagnostic | Unmapped vendor state string. |
| Raw total cleaned area | Sensor | `RobotStatus.total_cleaned_area_raw` | Disabled | Diagnostic | Unconverted vendor lifetime-area counter; its physical unit is not established. |
| Total cleaning time | Sensor | `RobotStatus.total_cleaning_time_seconds` | Disabled | Diagnostic | Vendor lifetime duration counter in seconds when reported. |
| Raw sub-state | Sensor | `RobotStatus.sub_state` | Disabled | Diagnostic | Vendor sub-state string without guessed interpretation. |
| Raw last sub-state | Sensor | `RobotStatus.last_sub_state` | Disabled | Diagnostic | Previous vendor sub-state string. |
| Raw position X / Y | Sensors | `RobotStatus.position_x`, `position_y` | Disabled | Diagnostic | Raw map coordinates; their physical unit is not established. |
| Raw heading | Sensor | `RobotStatus.heading` | Disabled | Diagnostic | Raw vendor heading value; its angular unit is not established. |
| Raw timer status | Sensor | `RobotStatus.timer_status` | Disabled | Diagnostic | Raw integer without guessed state mapping. |
| Raw auto boost | Sensor | `RobotStatus.auto_boost` | Disabled | Diagnostic | Raw integer without guessed state mapping. |
| Raw wiping assembly status | Sensor | `RobotStatus.mop_status` | Disabled | Diagnostic | Preserves model-dependent values not representable by the binary sensor. |

The robot model remains device metadata. Network information is available from
the library's explicit `get_network_info()` API but is not polled solely to feed
disabled entities.

## Room child device

| Entity | Platform | Source | Default | Category | Semantics and unknown behavior |
| --- | --- | --- | --- | --- | --- |
| Clean room | Button | `Room.id`, `clean_rooms()` | Enabled | None | Cleans exactly this room with its selected preferences. |
| Suction mode | Select | `Room.fan_mode` | Enabled | Config | Quiet, automatic, strong, or maximum. |
| Cleaning passes | Select | `Room.clean_times` | Enabled | Config | One or two passes. |
| Mopping water level | Select | `Room.water_pump` | Enabled when valid | Config | Only confirmed values `1` low, `2` medium, and `3` high are selectable. |
| Cleaning job selection | Switch | HA runtime state | Enabled | None | Temporary multi-room selection; not persisted. |
| Room ID | Sensor | `Room.id` | Disabled | Diagnostic | Robot/map-specific room identifier. |
| Room type | Sensor | `Room.room_type` | Disabled | Diagnostic | Vendor room type when reported. |
| Raw SweepArea mode | Sensor | `Room.mode` | Disabled | Diagnostic | Technical `SweepArea.mode` string used by observed carpet-related behavior; never used as sweep/mop mode. |
| Raw water pump | Sensor | `Room.water_pump` | Disabled | Diagnostic | Preserves unsupported values such as `0` or `4` without naming them. |
| Polygon vertex count | Sensor | `Room.vertices` | Disabled | Diagnostic | Number of room outline vertices; the full polygon is not exposed as attributes. |

Room entities retain robot-and-room based unique IDs and attach to the room
child device. The physical robot remains the parent device.

## Deliberately not exposed

- Consumables: no verified public consumables API currently exists.
- Firmware metadata: not present in the current public discovery model.
- Full polygons and path arrays: retained for future map work, not entity state.
- Opaque fields such as `modeIng`, `extendStatus`, `areaCleanMode`, and
  `BPStatus`: semantics are not established well enough for user-facing names.
- `waterPump` values `0` and `4`: visible only as raw disabled diagnostics and
  never offered as friendly selectable levels.
