# AGENTS.md

## Project

This repository contains a Home Assistant custom integration for Botslab / 360 robot vacuums.

Integration domain:

```text
botslab360
```

The reusable protocol and robot communication implementation MUST remain outside Home Assistant and is provided by the external Python package:

```text
botslab360
```

Repository:

```text
https://github.com/placix/python-botslab360
```

The required library version is declared in:

```text
custom_components/botslab360/manifest.json
```

Do not duplicate Botslab protocol logic inside the Home Assistant integration.

---

## Architecture rules

Keep this repository focused on Home Assistant integration concerns:

- config flows
- config entries
- entities
- services/actions
- coordinators
- translations
- device/entity registry integration
- reauthentication
- Home Assistant lifecycle behavior

Protocol and reusable API behavior belong in `python-botslab360`.

Do not implement directly in this repository:

- Botslab HTTP protocol internals
- TCP protocol
- push protocol
- AES decryption
- Qihoo authentication internals
- vendor command payload construction
- room/map protocol parsing that belongs in the reusable library

Use the public API of the `botslab360` Python package.

Do not access private library attributes when a clean public API can be provided instead.

---

## Home Assistant architecture

Use current Home Assistant config-entry patterns.

Prefer:

- Config Flow
- `entry.runtime_data`
- `DataUpdateCoordinator`
- native Home Assistant entity platforms
- proper Device Registry integration
- proper Entity Registry integration
- Home Assistant reauthentication
- translated errors and user-facing strings

Do not use YAML configuration for integration setup.

Existing YAML examples for service/action calls in documentation are fine.

---

## Current platforms

Currently active platforms are:

- vacuum
- sensor
- button

Room-cleaning buttons are native Home Assistant button entities.

Device and Area structure:

- the physical robot is the parent device
- each robot room is a Home Assistant child device
- each room-cleaning button belongs to its room child device
- exact normalized room/Area name matching is attempted automatically
- users review and override mappings during setup and through integration options
- mappings use stable robot/room keys and Home Assistant Area IDs
- explicit user mappings, including an unassigned room, must not be overwritten by
  later automatic matching

Each room button must:

- belong to the corresponding room child device below the vacuum
- use a stable unique ID containing robot/device ID and room ID
- use the room name for presentation
- call the public `client.clean_rooms(device, [room_id])` API
- target only its associated room
- refresh the coordinator after a successful command

Room IDs are robot/map-specific and must not be treated as globally unique.

---

## Authentication

Native authentication must survive ordinary Home Assistant restarts.

The intended authentication lifecycle is:

```text
email/password
    -> QUC authentication
    -> reusable Q/T cache
    -> Smart Home SID + pushKey
```

On later Home Assistant starts:

```text
cached Q/T
    -> fresh SID + pushKey
    -> normal runtime
```

Persist reusable Q/T credentials for native credential entries.

Do not persist SID or `pushKey` merely to survive Home Assistant restarts.

If cached Q/T becomes invalid:

1. attempt the native credential fallback once
2. update cached Q/T after a successful fallback
3. start Home Assistant reauthentication when interactive authentication is genuinely required

Legacy user-supplied Q/T entries must remain compatible.

Do not silently loop authentication attempts.

---

## Security

Never log, expose, commit, or include in diagnostics:

- Q
- T
- qid
- SID
- pushKey
- passwords
- captcha codes

Q and T are credentials.

Do not place real credentials in:

- source code
- tests
- documentation
- fixtures
- logs
- diagnostics
- screenshots intended for publication

Keep authentication-related diagnostics sanitized.

---

## Map status

Map functionality is intentionally deferred.

Keep the existing implementation in the repository, including:

- `camera.py`
- `map.py`
- existing rendering helpers
- map-related tests and support code where still useful

Unless a task explicitly concerns maps:

- do not enable the camera/map platform
- do not expose new map entities
- do not extend map functionality
- do not refactor the map subsystem
- do not remove the retained map implementation
- do not add Xiaomi Vacuum Map Card as a dependency
- do not implement No-Go zones
- do not implement room deletion
- do not implement room splitting or merging
- do not implement map editing

The map feature is retained for future work but is not part of the current active feature scope.

---

## Dependency management

The `botslab360` dependency is declared in:

```text
custom_components/botslab360/manifest.json
```

When changing its required version:

1. ensure the matching `python-botslab360` version exists
2. ensure it is installable from the package source used by Home Assistant
3. update `manifest.json`
4. run the complete integration test suite

Do not invent dependency versions.

---

## Versioning

The Home Assistant integration version belongs only in:

```text
custom_components/botslab360/manifest.json
```

Only change the integration version when:

- explicitly requested
- a target release version has already been agreed
- the task is clearly part of preparing that release

Do not place version values in unrelated configuration files.

Do not invent version numbers.

---

## Scope discipline

Before changing code:

1. inspect the existing implementation
2. inspect the public API of the required `botslab360` package
3. follow existing Home Assistant architectural conventions
4. keep the integration thin
5. prefer tests over speculative protocol behavior

Do not perform unrelated:

- refactors
- formatting sweeps
- dependency upgrades
- line-ending normalization
- migrations

Preserve existing config-entry and entity compatibility unless the task explicitly requires a migration.

Do not remove deferred functionality merely because it is currently disabled.

---

## Validation

Before considering a code task complete, run as applicable:

```text
ruff check .
ruff format --check .
python -m pytest -q -p no:cacheprovider
python -m compileall custom_components tests
git diff --check
```

When JSON files are changed, also validate:

- `manifest.json`
- `strings.json`
- translation JSON files

Expected result:

- Ruff passes
- format check passes
- all tests pass
- `compileall` succeeds
- JSON is valid
- `git diff --check` succeeds

LF/CRLF informational warnings on Windows are not by themselves a reason to rewrite files or normalize line endings.

---

## Git workflow

At the beginning of a task:

- inspect `git status`
- preserve existing user changes
- never discard unrelated work

At the end of a completed code task:

1. review the complete `git diff`
2. verify no credentials, temporary files, or unrelated changes are included
3. run all relevant validation
4. commit only when the user explicitly requests it
5. when a commit is requested, stage only the intended files and create one
   focused commit using a concise Conventional Commit style message
6. report as applicable:
   - commit hash
   - commit message
   - validation results
   - whether the working tree contains pre-existing or unrelated changes

Do not:

- commit unless explicitly requested by the user
- push
- create tags
- create GitHub releases
- publish packages
- rewrite remote history

unless explicitly requested by the user.
