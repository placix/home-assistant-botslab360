"""Fixtures for Botslab 360 tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from botslab360 import Botslab360Client, Device, RobotStatus
import pytest

from custom_components.botslab360.const import CONF_Q, CONF_T

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_ACCOUNT_FINGERPRINT = "test-account-fingerprint"
TEST_CREDENTIALS = {
    CONF_Q: "not-a-real-q-token",
    CONF_T: "not-a-real-t-token",
}
TEST_DEVICE = Device(
    id="test-device-id",
    name="Test Robot",
    model="Test Model",
    online=True,
)


def make_status(
    *,
    state: str | None = "idle",
    error_code: int = 0,
) -> RobotStatus:
    """Create a representative robot status."""

    return RobotStatus(
        device_id=TEST_DEVICE.id,
        online=True,
        battery=73,
        state=state,
        charging=False,
        fan_mode="strong",
        cleaned_area_m2=42,
        cleaning_time_seconds=321,
        error_code=error_code,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in tests."""

    yield


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Return a fully mocked botslab360 client."""

    client = MagicMock(spec=Botslab360Client)
    client.account_fingerprint = TEST_ACCOUNT_FINGERPRINT
    client.authenticate = AsyncMock()
    client.get_devices = AsyncMock(return_value=[TEST_DEVICE])
    client.get_status = AsyncMock(return_value=make_status())
    client.start_cleaning = AsyncMock()
    client.pause = AsyncMock()
    client.resume = AsyncMock()
    client.return_to_dock = AsyncMock()
    client.locate = AsyncMock()
    client.close = AsyncMock()

    with (
        patch(
            "custom_components.botslab360.config_flow.Botslab360Client",
            return_value=client,
        ),
        patch(
            "custom_components.botslab360.Botslab360Client",
            return_value=client,
        ),
    ):
        yield client
