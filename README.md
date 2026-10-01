# Botslab 360 for Home Assistant

[![Validate](https://github.com/placix/home-assistant-botslab360/actions/workflows/validate.yml/badge.svg)](https://github.com/placix/home-assistant-botslab360/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Botslab 360 is an unofficial Home Assistant custom integration for Botslab / 360
robot vacuums. It uses the external
[`botslab360`](https://github.com/placix/python-botslab360) Python package for
communication with Botslab services and devices.

The integration is under active development. Native 360Robot authentication
and the current vacuum and status features have been tested with a 360Robot
S9-P. Other models may behave differently.

## Features

- native email and password authentication for Botslab / CloudSmart and
  360Robot accounts;
- captcha continuation when required by the selected account service;
- robot discovery, including a narrow DHCP match for the 360 CleanRobot X9 /
  S9-P network signature;
- vacuum controls for start, pause, resume, return to dock, and locate;
- battery, cleaned area, cleaning duration, error, fan-mode, and wiping-assembly
  information;
- room-cleaning buttons and temporary multi-room cleaning jobs;
- per-room suction, pass-count, and water-level controls;
- robot cleaning modes for sweep, sweep and mop, and mop only;
- continued support for existing legacy Q/T config entries.

Everyday entities are enabled by default. Technical diagnostics are registered
but disabled by default so they can be enabled selectively. See the
[entity inventory](docs/entities.md) for the complete matrix and
unknown-value behavior.

## Installation

### HACS custom repository

1. In HACS, open **Integrations**, then the menu and **Custom repositories**.
2. Add `https://github.com/placix/home-assistant-botslab360` as an
   **Integration** repository.
3. Install **Botslab 360** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, search for
   **Botslab 360**, and add it.

### Manual

Copy `custom_components/botslab360` into the `custom_components` directory in
your Home Assistant configuration, restart Home Assistant, then add the
integration from **Settings → Devices & services**.

## Requirements and setup

The robot must belong to either a Botslab / CloudSmart account or an original
360Robot account. These services use different account systems, so select the
account type that owns the robot; the integration never falls back between
them automatically.

Enter the account email address and password in the config flow. If the account
service requests verification, enter the captcha code displayed by Home
Assistant. Existing entries that use legacy Q/T credentials remain supported.

When Home Assistant detects the narrowly matched 360 CleanRobot X9 DHCP
signature, setup still requires explicit confirmation and account sign-in. The
integration compares the discovered MAC address with the robots returned by the
authenticated account. An exact unique match associates the address with that
robot. If verification is unavailable or inconclusive, setup continues without
guessing and retries later. The robot's current IP address is not stored.

## Rooms and controls

The integration discovers rooms through `python-botslab360` and creates a
native Home Assistant button for each room. Pressing it cleans exactly that
room using its selected suction, pass count, and water level. Room entities are
grouped on a child device below their vacuum and remain stable when room names
change.

The physical robot device also provides one temporary job-selection switch per
active room plus buttons to start or clear the selection. Starting the job
sends all selected room IDs in a single multi-room request and applies each
room's current settings. The robot determines the navigation order. Selections
are runtime-only, reset on integration reload or restart, and exclude ignored
rooms.

The `botslab360.clean_rooms` action remains available for automations that need
to select one or more room IDs or apply supported per-run settings.

The robot cleaning-mode select follows the reported wiping assembly. Without
the assembly, **Sweep** is available. With it installed, **Sweep and mop** and
**Mop** are available, while **Sweep** is rejected until the assembly is
removed. The robot does not report the current mop-only switch in its known
status payload, so after a restart the select remains unknown until a mode is
selected successfully. This optimistic state is not persisted. Water level is
an independent per-room setting.

## Authentication and security

Email addresses, passwords, captcha codes, and legacy Q/T session credentials
are sensitive. Do not share, publish, commit, or log them.

The integration stores a generated device identity in the Home Assistant config
entry and reuses it during reauthentication. Native email/password entries also
cache reusable Q/T credentials so a restart can establish a fresh Smart Home
session without repeating the interactive login. SID and push-key session
values are not persisted. If the cache is rejected, the integration retries
once with the stored native credentials and refreshes the cache after a
successful authentication.

## Architecture and compatibility

All device and protocol communication is delegated to the separately released
[`python-botslab360`](https://github.com/placix/python-botslab360) library. The
integration currently pins `botslab360==0.7.0` and exposes the library through
Home Assistant-native entities, actions, config flows, and diagnostics.

The vendor `SweepArea.mode` field is deliberately not exposed as a cleaning
mode: Android app analysis identifies it as a nullable carpet-related value,
not a verified room sweep/mop selector.

## Known limitations

- Only the 360Robot S9-P has been verified with the current authentication,
  status, room, and control features.
- The current mop-only state cannot be recovered from the known robot status
  payload and is therefore not guessed or persisted.
- The experimental map renderer remains in the codebase, but the camera
  platform and map camera entities are disabled. Xiaomi Vacuum Map Card support
  and additional map features are deferred.
- Network discovery never guesses a robot association when MAC verification is
  unavailable or ambiguous.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
python -m pytest -q -p no:cacheprovider
```

Special thanks to [TA2k](https://github.com/TA2k) and the
[ioBroker.botslab360](https://github.com/TA2k/ioBroker.botslab360) project for
protocol research and implementation references. This is an independent
project; TA2k is not one of its maintainers. The integration icon is derived
from that MIT-licensed project's icon; see [ATTRIBUTION.md](ATTRIBUTION.md).

Version `0.6.2` is licensed under the [MIT License](LICENSE).
