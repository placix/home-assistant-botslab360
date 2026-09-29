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
- Narrow DHCP discovery for the 360 CleanRobot X9 / S9-P network signature
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
- Native room-cleaning buttons
- Native per-room suction, pass-count, and water-level controls
- Room cleaning through `botslab360.clean_rooms`

This is an early integration. Native 360Robot authentication and the current
vacuum/status feature set have been tested with a 360Robot S9-P. Other models
may behave differently.

Botslab / CloudSmart and original 360Robot accounts use different account
systems. Select the account type that owns the robot; the integration does not
automatically fall back between them.

When Home Assistant detects the narrowly matched 360 CleanRobot X9 DHCP
signature, setup still requires explicit confirmation and account sign-in. The
integration compares the discovered network MAC with each robot returned by the
authenticated account. An exact unique match associates the MAC with that
physical robot. If network verification is unavailable or inconclusive, account
setup continues without guessing the association and retries it during a later
setup. The robot's current IP address is not stored.

## Room cleaning

The integration discovers rooms through `python-botslab360` and creates one
native Home Assistant button for each room. Pressing a room button starts a
cleaning run for exactly that room using its selected suction, pass count, and
water level. Room controls are grouped on a child device below
their vacuum and remain stable when room names change.

The vendor `SweepArea.mode` field is not exposed as a cleaning-mode control. In
the analyzed Android app it is a nullable string used by carpet-related
behavior, not a verified room Sweep/Mop selector.

The `botslab360.clean_rooms` action remains available for automations that need
to select one or more room IDs or apply supported per-run cleaning settings.

## Map support

The existing experimental map renderer remains in the codebase for future
development, but the camera platform is currently disabled and no map camera
entities are exposed. Xiaomi Vacuum Map Card integration and additional map
features are explicitly deferred.

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

For native email/password entries, Home Assistant also caches the reusable Q/T
credentials obtained after successful authentication. Restarts use that cache
to establish a fresh Smart Home session without repeating the interactive
account login. SID and push-key session values are not persisted. If the cache
is rejected, the integration falls back once to the stored native credentials
and updates the cache after successful authentication.

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
