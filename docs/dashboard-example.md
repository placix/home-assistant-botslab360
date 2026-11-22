# Room-cleaning dashboard example

This anonymized example combines the integration's standard Home Assistant
entities in a Sections dashboard. It uses only built-in cards and features;
no custom cards or additional integrations are required.

**Replace every example entity ID with the actual ID created in your Home
Assistant installation before using this dashboard.** All `my_robot` IDs below
are documentation placeholders. The integration does not guarantee these exact
names. Actual IDs depend on device names, room names, and the Home Assistant
entity registry. Find them under **Settings > Devices & services > Entities**.

## How the controls work

- Room entities depend on the rooms reported by the robot. Ignored rooms are
  excluded; a model without room segmentation has no room controls.
- The Cleaning job switches select rooms for a temporary multi-room cleaning
  job. Toggling them does not start cleaning. Selection is runtime-only and is
  reset on integration reload or Home Assistant restart.
- Start cleaning submits all selected rooms as one cleaning job, using each
  room's settings. Select at least one room first. The robot chooses navigation
  order; successful submission clears the selection.
- Clear selection only clears the pending selection; it does not stop a robot
  that is already cleaning.
- Each room's Clean now button immediately starts cleaning only that room,
  independently of the job-selection switches. No prior job selection is needed.
- Suction, pass count, and water level selectors set preferences associated with
  that room. These preferences are applied when its next cleaning job starts.
- Remove cards for entities your model does not expose, including unsupported
  water-level controls. Do not create helpers just to fill missing placeholders.

## Complete Sections dashboard

Use the following as the raw configuration of a dedicated dashboard. If you
already have a dashboard with a `views:` list, append the view below to that list
instead of replacing your existing configuration. The README's shorter example
is a single section, not a complete dashboard.

Kitchen, Bathroom, Hallway, and Living room are generic example rooms, not a
representation of a particular installation. Remove or duplicate room sections
and selection tiles to match your robot's actual rooms.

```yaml
views:
  - type: sections
    title: Robot vacuum
    path: robot-vacuum
    icon: mdi:robot-vacuum
    max_columns: 4
    sections:
      - type: grid
        cards:
          - type: heading
            heading: Cleaning job
            icon: mdi:clipboard-list-outline
          - type: tile
            entity: switch.my_robot_job_kitchen
            name: Kitchen
            icon: mdi:stove
            tap_action:
              action: toggle
          - type: tile
            entity: switch.my_robot_job_bathroom
            name: Bathroom
            icon: mdi:shower
            tap_action:
              action: toggle
          - type: tile
            entity: switch.my_robot_job_hallway
            name: Hallway
            icon: mdi:door
            tap_action:
              action: toggle
          - type: tile
            entity: switch.my_robot_job_living_room
            name: Living room
            icon: mdi:sofa
            tap_action:
              action: toggle
          - type: button
            entity: button.my_robot_start_selected_rooms
            name: Start cleaning
            icon: mdi:play-circle
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_start_selected_rooms
          - type: button
            entity: button.my_robot_clear_room_selection
            name: Clear selection
            icon: mdi:selection-remove
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_clear_room_selection
      - type: grid
        cards:
          - type: heading
            heading: Robot status
            icon: mdi:robot-vacuum
          - type: tile
            entity: vacuum.my_robot
            name: Robot
            tap_action:
              action: more-info
          - type: tile
            entity: sensor.my_robot_battery
            name: Battery
          - type: tile
            entity: binary_sensor.my_robot_mop_attached
            name: Mop attachment
          - type: tile
            entity: sensor.my_robot_cleaned_area
            name: Cleaned area
          - type: tile
            entity: sensor.my_robot_cleaning_time
            name: Cleaning time
          - type: tile
            entity: sensor.my_robot_fan_mode
            name: Fan mode
          - type: tile
            entity: sensor.my_robot_error
            name: Error
      - type: grid
        cards:
          - type: heading
            heading: Kitchen
            icon: mdi:stove
          - type: button
            entity: button.my_robot_kitchen_clean
            name: Clean now
            icon: mdi:broom
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_kitchen_clean
          - type: tile
            entity: select.my_robot_kitchen_suction
            name: Suction
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_kitchen_passes
            name: Pass count
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_kitchen_water
            name: Water level
            features:
              - type: select-options
      - type: grid
        cards:
          - type: heading
            heading: Bathroom
            icon: mdi:shower
          - type: button
            entity: button.my_robot_bathroom_clean
            name: Clean now
            icon: mdi:broom
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_bathroom_clean
          - type: tile
            entity: select.my_robot_bathroom_suction
            name: Suction
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_bathroom_passes
            name: Pass count
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_bathroom_water
            name: Water level
            features:
              - type: select-options
      - type: grid
        cards:
          - type: heading
            heading: Hallway
            icon: mdi:door
          - type: button
            entity: button.my_robot_hallway_clean
            name: Clean now
            icon: mdi:broom
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_hallway_clean
          - type: tile
            entity: select.my_robot_hallway_suction
            name: Suction
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_hallway_passes
            name: Pass count
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_hallway_water
            name: Water level
            features:
              - type: select-options
      - type: grid
        cards:
          - type: heading
            heading: Living room
            icon: mdi:sofa
          - type: button
            entity: button.my_robot_living_room_clean
            name: Clean now
            icon: mdi:broom
            show_state: false
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.my_robot_living_room_clean
          - type: tile
            entity: select.my_robot_living_room_suction
            name: Suction
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_living_room_passes
            name: Pass count
            features:
              - type: select-options
          - type: tile
            entity: select.my_robot_living_room_water
            name: Water level
            features:
              - type: select-options
```

The vacuum tile opens the robot's more-info dialog; it does not start a general
cleaning run. Robot-status cards show current runtime information, while the
per-room selectors describe preferences for subsequent room-cleaning jobs.

For Home Assistant's layout and card configuration details, see
[Sections](https://www.home-assistant.io/dashboards/sections/),
[Button card](https://www.home-assistant.io/dashboards/button/), and
[Select options](https://www.home-assistant.io/dashboards/features/#select-options).

See [Rooms and controls](../README.md#rooms-and-controls) for integration behavior.
