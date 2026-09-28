# Botslab 360 for Home Assistant

Botslab 360 is an early, unofficial Home Assistant custom integration for
Botslab / 360 robot vacuums. It uses the external `botslab360` Python package
for all communication with Botslab services and devices.

## Supported features

- Native email/password authentication
- Explicit Botslab / CloudSmart and 360Robot account selection
- Captcha continuation when required by the selected account service
- Continued support for existing legacy Q/T config entries
- Robot discovery
- Vacuum entity
- Start
- Pause
- Resume
- Return to dock
- Locate
- Battery level
- Cleaned area
- Cleaning duration
- Error code
- Fan mode
- Rendered room-polygon map camera
- Room cleaning through `botslab360.clean_rooms`

This is an early integration. Native 360Robot authentication and the current
vacuum/status feature set have been tested with a 360Robot S9-P. Other models
may behave differently.

Botslab / CloudSmart and original 360Robot accounts use different account
systems. Select the account type that owns the robot; the integration does not
automatically fall back between them.

## Xiaomi Vacuum Map Card

The integration creates a rendered map camera for every robot, for example
`camera.test_robot_map`. This is a room-polygon cleaning map, not a physical
camera or live video stream. Its `calibration_points` attribute uses the same
coordinate transform as the PNG, so the Xiaomi Vacuum Map Card can request
camera calibration directly.

Room geometry changes infrequently. The image is fetched and cached on first
use and can be refreshed explicitly with `homeassistant.update_entity`; it is
not refreshed by the 60-second vacuum status poll. The camera's
`predefined_selections` attribute contains a compact, ready-to-copy list of
room IDs, outlines, and centroid labels for the card's `ROOM` mode.

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.test_robot
map_source:
  camera: camera.test_robot_map
calibration_source:
  camera: true
map_modes:
  - name: Rooms
    icon: mdi:floor-plan
    selection_type: ROOM
    max_selections: 10
    repeats_type: EXTERNAL
    max_repeats: 2
    predefined_selections:
      # Copy the current list from the map camera attribute of the same name.
      - id: 1
        outline:
          - [0, 0]
          - [4000, 0]
          - [4000, 3000]
          - [0, 3000]
        label:
          text: Example room
          x: 2000
          y: 1500
    service_call_schema:
      service: botslab360.clean_rooms
      target:
        entity_id: "[[entity_id]]"
      service_data:
        room_ids: "[[selection]]"
        clean_times: "[[repeats]]"
```

The example outline is illustrative. Use the coordinates exposed by your own
map camera. The initial renderer intentionally shows room polygons only; it
does not decode the vendor occupancy raster or render walls, paths, the robot,
or the charging dock.

## Installation with HACS

1. Open HACS in Home Assistant.
2. Open the HACS menu and select **Custom repositories**.
3. Enter `https://github.com/placix/home-assistant-botslab360` as the repository.
4. Select **Integration** as the category and add the repository.
5. Find **Botslab 360** in HACS and install it.
6. Restart Home Assistant.
7. Open **Settings > Devices & services**, select **Add integration**, and add
   **Botslab 360**.
8. Select **360Robot** or **Botslab / CloudSmart**, then enter the account email
   address and password.
9. If the account service requests verification, enter the code shown in the
   config flow.

## Credential security

Email addresses, passwords, captcha codes, and legacy Q/T session credentials
are sensitive. Do not share, publish, commit, or log them.

The integration stores a generated device identity in the Home Assistant config
entry and reuses it during reauthentication. The external library does not
persist this identity itself. Existing config entries that use Q/T credentials
remain supported and continue to reauthenticate through their legacy flow.

## Acknowledgements

Special thanks to [TA2k](https://github.com/TA2k) and the
[ioBroker.botslab360](https://github.com/TA2k/ioBroker.botslab360) project for
protocol research and implementation references that helped make Botslab /
360Robot support possible.

This Home Assistant integration is an independent project and uses the
separate `python-botslab360` library for device and protocol communication.
TA2k is not a maintainer of this integration.

## Brand asset

The integration icon is derived from the icon used by the MIT-licensed
`TA2k/ioBroker.botslab360` project. See [ATTRIBUTION.md](ATTRIBUTION.md) for its
source and license notice.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
