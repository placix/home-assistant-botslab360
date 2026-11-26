# Botslab 360 for Home Assistant 0.6.9-beta.4

This prerelease is intended for testing recovery from intermittent push
registration failures during status polling.

## What's changed

- Use `botslab360==0.7.6b2` for bounded registration recovery with backoff.
- Allow one final status attempt when the first retry ends in remote EOF
  during registration and sufficient time remains.
- Keep the 125-second per-robot status budget, including time spent waiting
  for another status query on the same client.
- Keep the regular polling interval at 60 seconds after refresh completion.
- Report the installed library version at config-entry setup using the
  dedicated DEBUG diagnostics logger.

## Testing and compatibility

Home Assistant 2026.9.0 or later is required. Room, map and robot control
behaviour is unchanged. This release adds no automatic command retries.

To capture retry decisions, push cleanup and the installed package version:

```yaml
logger:
  logs:
    custom_components.botslab360.diagnostics: debug
    botslab360.client: debug
    botslab360.protocol: debug
```

The cause of the observed early disconnect remains under investigation.
During a cloud outage, a refresh can consume the full status budget, plus
cleanup, before the next regular polling interval begins.
