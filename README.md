# Botslab 360 for Home Assistant

Botslab 360 is an early, unofficial Home Assistant custom integration for
Botslab / 360 robot vacuums. It uses the external `botslab360` Python package
for all communication with Botslab services and devices.

## Supported features

- Q/T authentication
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

This is an early first version. Its supported devices and behavior may still
change as the integration is tested with more robot models.

## Installation with HACS

1. Open HACS in Home Assistant.
2. Open the HACS menu and select **Custom repositories**.
3. Enter `https://github.com/placix/home-assistant-botslab360` as the repository.
4. Select **Integration** as the category and add the repository.
5. Find **Botslab 360** in HACS and install it.
6. Restart Home Assistant.
7. Open **Settings > Devices & services**, select **Add integration**, and add
   **Botslab 360**.
8. Enter the Q and T credentials when prompted.

## Credential security

Q and T are sensitive session credentials. Do not share, publish, commit, or
log them. Treat them like passwords.

## Brand asset

The integration icon is derived from the icon used by the MIT-licensed
`TA2k/ioBroker.botslab360` project. See [ATTRIBUTION.md](ATTRIBUTION.md) for its
source and license notice.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
