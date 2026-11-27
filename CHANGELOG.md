# Changelog

## 0.6.9-beta.5

This prerelease tests extended network and HTTP status recovery.

- Update the dependency to exactly `botslab360==0.7.6b3`.
- Recover from selected socket and temporary DNS failures and HTTP
  502/503/504 within the existing 125-second per-robot status budget.
  HTTP 429 requires a valid Retry-After that fits the remaining budget to retry.
- During a valid Retry-After cooldown from HTTP 429 or 503, later status
  polls for the same client fail locally without push or HTTP traffic.
- Keep the 60-second polling interval and existing coordinator behaviour.
  Robot control commands receive no additional retries.
- The dedicated DEBUG loggers document transport causes and retry decisions.
  Real-world validation of the new backoffs is still pending.

## 0.6.9-beta.4

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
