"""Fixtures for Botslab 360 tests."""

from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botslab360 import (
    Botslab360Client,
    Device,
    DeviceIdentity,
    QihooCredentials,
    RobotStatus,
    SmartSession,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from custom_components.botslab360.const import (
    CONF_AUTH_BACKEND,
    CONF_DEVICE_IDENTITY,
    CONF_IDENTITY_ANDROID_ID,
    CONF_IDENTITY_M2,
    CONF_IDENTITY_MID,
    CONF_Q,
    CONF_T,
)

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_ACCOUNT_FINGERPRINT = "test-account-fingerprint"
TEST_CREDENTIALS = {
    CONF_Q: "not-a-real-q-token",
    CONF_T: "not-a-real-t-token",
}
TEST_DEVICE_IDENTITY = DeviceIdentity(
    mid="0123456789abcdef0123456789abcdef",
    android_id="0123456789abcdef",
    m2="00000000-0000-4000-8000-000000000001",
)
TEST_NATIVE_INPUT = {
    CONF_AUTH_BACKEND: "robot360",
    CONF_EMAIL: "user@example.invalid",
    CONF_PASSWORD: "not-a-real-password",
}
TEST_NATIVE_ENTRY_DATA = {
    **TEST_NATIVE_INPUT,
    CONF_DEVICE_IDENTITY: {
        CONF_IDENTITY_MID: TEST_DEVICE_IDENTITY.mid,
        CONF_IDENTITY_ANDROID_ID: TEST_DEVICE_IDENTITY.android_id,
        CONF_IDENTITY_M2: TEST_DEVICE_IDENTITY.m2,
    },
}
TEST_DEVICE = Device(
    id="test-device-id",
    name="Test Robot",
    model="Test Model",
    online=True,
)
TEST_SESSION = SmartSession(
    qid="synthetic-qid",
    sid="synthetic-sid",
    push_key="synthetic-push-key",
)
TEST_CACHED_CREDENTIALS = QihooCredentials(
    q="u=360H1234567890&n=synthetic&m=cached-token",
    t="s=cached-session&t=1700000000&v=2.0",
    qid="1234567890",
)


@dataclass(frozen=True, slots=True)
class TestRoom:
    """Room-shaped test value including the upcoming geometry field."""

    id: int
    name: str
    room_type: str | None
    clean_times: int | None
    fan_mode: str | None
    water_pump: int | None
    vertices: tuple[tuple[int, int], ...] | None
    mode: int | None


TEST_ROOMS = [
    TestRoom(
        id=1,
        name="Bad",
        room_type="bathroom",
        clean_times=2,
        fan_mode="max",
        water_pump=1,
        vertices=((0, 0), (4000, 0), (4000, 3000), (0, 3000)),
        mode=2,
    ),
    TestRoom(
        id=6,
        name="Closet",
        room_type=None,
        clean_times=1,
        fan_mode="auto",
        water_pump=1,
        vertices=((4000, 0), (6500, 0), (6500, 3000), (4000, 3000)),
        mode=1,
    ),
]


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


def make_mock_client() -> MagicMock:
    """Create a client mock with realistic authentication state."""

    client = MagicMock(spec=Botslab360Client)
    client.account_fingerprint = TEST_ACCOUNT_FINGERPRINT
    client.credentials = TEST_CACHED_CREDENTIALS
    client.session = None

    async def authenticate(*args, **kwargs):
        client.session = TEST_SESSION
        return TEST_SESSION

    async def continue_authentication(*args, **kwargs):
        client.session = TEST_SESSION
        return TEST_SESSION

    client.authenticate = AsyncMock(side_effect=authenticate)
    client.continue_authentication = AsyncMock(side_effect=continue_authentication)
    client.get_devices = AsyncMock(return_value=[TEST_DEVICE])
    client.get_rooms = AsyncMock(return_value=TEST_ROOMS)
    client.clean_rooms = AsyncMock()
    client.get_status = AsyncMock(return_value=make_status())
    client.start_cleaning = AsyncMock()
    client.pause = AsyncMock()
    client.resume = AsyncMock()
    client.return_to_dock = AsyncMock()
    client.locate = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Return a fully mocked botslab360 client."""

    client = make_mock_client()

    with (
        patch(
            "custom_components.botslab360.config_flow.create_client_from_entry_data",
            return_value=client,
        ),
        patch(
            "custom_components.botslab360.config_flow.create_native_client_from_entry_data",
            return_value=client,
        ),
        patch(
            "custom_components.botslab360.create_client_from_entry_data",
            return_value=client,
        ),
        patch(
            "custom_components.botslab360.config_flow.DeviceIdentity.generate",
            return_value=TEST_DEVICE_IDENTITY,
        ),
    ):
        yield client
