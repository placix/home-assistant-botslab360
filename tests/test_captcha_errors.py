"""Stage-aware captcha continuation errors and secret-free diagnostics."""

import logging
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
from botslab360 import (
    ApiError,
    AuthBackend,
    AuthenticationError,
    CaptchaChallenge,
    CaptchaRequired,
    InvalidSessionError,
    QucAuthenticationError,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.botslab360.config_flow import (
    CONF_CAPTCHA_CODE,
    _log_authentication_result,
)
from custom_components.botslab360.const import DOMAIN

from .conftest import (
    TEST_ACCOUNT_FINGERPRINT,
    TEST_CACHED_CREDENTIALS,
    TEST_DEVICE,
    TEST_NATIVE_ENTRY_DATA,
    TEST_NATIVE_INPUT,
    TEST_ROOMS,
)
from .test_config_flow import TEST_CAPTCHA, TEST_CAPTCHA_CODE, _submit_user_flow

LOGGER = "custom_components.botslab360.config_flow"


@pytest.mark.parametrize(
    "cause_type",
    [httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout],
)
async def test_discovery_transport_type_is_logged(
    hass, mock_client, caplog, cause_type
):
    """A real library-style cause survives discovery classification without its text."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    captcha = await _submit_user_flow(hass)
    error = ApiError("synthetic-private-wrapper", phase="transport")
    error.__cause__ = cause_type(
        "synthetic-private-message",
        request=httpx.Request("GET", "https://example.invalid/synthetic-private-url"),
    )
    mock_client.get_devices.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert "stage=device_list result=failed phase=transport" in caplog.text
    assert f"transport_error_type={cause_type.__name__}" in caplog.text
    assert "synthetic-private" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("kind", ["custom", "subclass", "missing", "timeout"])
def test_transport_diagnostics_allowlist(mock_client, caplog, kind):
    """Unknown names and chained exception messages are never printed."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    error = ApiError("synthetic-private-message", phase="transport")
    expected = "unknown"
    if kind in {"custom", "subclass"}:
        base = httpx.ReadError if kind == "subclass" else Exception
        error.__cause__ = type("synthetic-private-class", (base,), {})(
            "synthetic-private-cause"
        )
        expected = "ReadError" if kind == "subclass" else "unknown"
    elif kind == "missing":
        expected = "None"
    else:
        error = TimeoutError("synthetic-private-message")
        expected = "TimeoutError"
    _log_authentication_result(mock_client, "discovery", "failed", error)
    assert f"transport_error_type={expected}" in caplog.text
    assert "synthetic-private" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def _quc_error(errno, *, phase="quc-login"):
    return QucAuthenticationError(
        "synthetic-private-error",
        region=None,
        errno=errno,
        errmsg="synthetic-private-server-message",
        status_code=200,
        user_present=False,
        captcha_required=False,
        captcha_type="graph",
        phase=phase,
    )


@pytest.mark.parametrize("reauth", [False, True])
@pytest.mark.parametrize(
    ("error", "quc_succeeded", "expected", "stage"),
    [
        (_quc_error(5011), False, "invalid_captcha", "quc"),
        (_quc_error(1036), False, "invalid_auth", "quc"),
        (_quc_error(103), False, "invalid_auth", "quc"),
        (_quc_error(0, phase="response-validation"), False, "cannot_connect", "quc"),
        (
            AuthenticationError("input failed", phase="input"),
            False,
            "invalid_auth",
            "quc",
        ),
        (
            AuthenticationError("unauthorized", errno=103, status_code=200),
            True,
            "session_auth_failed",
            "smart_home",
        ),
        (
            InvalidSessionError("invalid session", errno=102, status_code=200),
            True,
            "session_auth_failed",
            "smart_home",
        ),
        (
            ApiError("rejected", errno=999, status_code=200),
            True,
            "cannot_connect",
            "smart_home",
        ),
        (
            ApiError("bad response", phase="json", status_code=200),
            True,
            "cannot_connect",
            "smart_home",
        ),
        (TimeoutError("private transport details"), False, "cannot_connect", "quc"),
        (OSError("private transport details"), True, "cannot_connect", "smart_home"),
    ],
)
async def test_captcha_failure_stage(
    hass, mock_client, caplog, reauth, error, quc_succeeded, expected, stage
) -> None:
    """Only QUC captcha rejections retain the challenge; later failures release it."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    mock_client.auth_backend = AuthBackend.ROBOT360
    mock_client.credentials = None
    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    if reauth:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_ACCOUNT_FINGERPRINT,
            data=TEST_NATIVE_ENTRY_DATA,
        )
        entry.add_to_hass(hass)
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        captcha = await hass.config_entries.flow.async_configure(
            initial["flow_id"],
            {key: TEST_NATIVE_INPUT[key] for key in ("email", "password")},
        )
    else:
        captcha = await _submit_user_flow(hass)

    async def fail(challenge, code):
        assert challenge is TEST_CAPTCHA
        assert code == TEST_CAPTCHA_CODE
        if quc_succeeded:
            mock_client.credentials = TEST_CACHED_CREDENTIALS
        raise error

    mock_client.continue_authentication.side_effect = fail
    caplog.clear()
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert f"backend=robot360 stage={stage} result=failed" in caplog.text
    assert ("stage=quc result=succeeded" in caplog.text) == quc_succeeded
    if expected == "session_auth_failed":
        assert f"errno={error.errno} response_errno={error.errno}" in caplog.text
        assert "http_status=200" in caplog.text
    mock_client.get_devices.assert_not_awaited()
    mock_client.continue_authentication.assert_awaited_once()
    mock_client.authenticate.assert_awaited_once()
    if expected == "invalid_captcha":
        assert result["step_id"] == "captcha"
        mock_client.close.assert_not_awaited()
    else:
        assert result["step_id"] == ("reauth_confirm" if reauth else "user")
        mock_client.close.assert_awaited_once()
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
        mock_client.close.assert_awaited_once()
    for secret in (
        TEST_CAPTCHA.sc,
        TEST_CAPTCHA_CODE,
        TEST_CACHED_CREDENTIALS.q,
        TEST_CACHED_CREDENTIALS.t,
        TEST_CACHED_CREDENTIALS.qid,
        TEST_NATIVE_INPUT["email"],
        TEST_NATIVE_INPUT["password"],
        str(error),
        "synthetic-private-server-message",
    ):
        assert secret not in caplog.text


async def test_new_captcha_replaces_challenge(hass, mock_client, caplog) -> None:
    """A repeated captcha request retains the client and presents the new image."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    mock_client.credentials = None
    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    captcha = await _submit_user_flow(hass)
    replacement = CaptchaChallenge(image=b"replacement-image", sc="private-new-context")
    mock_client.continue_authentication.side_effect = CaptchaRequired(replacement)
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
    )
    assert result["step_id"] == "captcha"
    assert result["errors"] == {"base": "invalid_captcha"}
    mock_client.close.assert_not_awaited()
    mock_client.continue_authentication.side_effect = _quc_error(5011)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
    )
    assert mock_client.continue_authentication.await_args.args[0] is replacement
    assert replacement.sc not in caplog.text
    assert "stage=quc result=captcha_required" in caplog.text


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, "no_devices"),
        (ApiError("discovery failure"), "cannot_connect"),
        (AuthenticationError("discovery auth"), "invalid_auth"),
        (InvalidSessionError("discovery session", errno=102), "cannot_connect"),
    ],
)
async def test_discovery_after_captcha_is_separate(
    hass, mock_client, caplog, error, expected
) -> None:
    """Discovery failures after successful login are not captcha/session-login failures."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    captcha = await _submit_user_flow(hass)
    mock_client.get_devices.side_effect = error
    mock_client.get_devices.return_value = []
    result = await hass.config_entries.flow.async_configure(
        captcha["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}
    assert "stage=smart_home result=succeeded" in caplog.text
    assert "stage=device_list result=" in caplog.text
    assert "stage=rooms" not in caplog.text
    if error is None:
        assert "stage=device_list result=no_devices" in caplog.text
        assert "device_count=0" in caplog.text
    mock_client.close.assert_awaited_once()


@pytest.mark.parametrize("captcha_required", [False, True])
@pytest.mark.parametrize("failed_stage", ["device_list", "rooms"])
async def test_discovery_failure_identifies_actual_stage(
    hass, mock_client, caplog, captcha_required, failed_stage
) -> None:
    """Both entry paths locate unchained push/API transport failures precisely."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    error = ApiError("synthetic-private-push-failure", phase="transport")
    if failed_stage == "device_list":
        mock_client.get_devices.side_effect = error
    else:
        mock_client.get_rooms.side_effect = error
    if captcha_required:
        mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    result = await _submit_user_flow(hass)
    if captcha_required:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
        )
    assert result["errors"] == {"base": "cannot_connect"}
    assert f"stage={failed_stage} result=failed phase=transport" in caplog.text
    assert "transport_error_type=None" in caplog.text
    assert "stage=discovery" not in caplog.text
    if failed_stage == "device_list":
        assert "stage=rooms" not in caplog.text
        mock_client.get_rooms.assert_not_awaited()
    else:
        assert "stage=device_list result=succeeded" in caplog.text
        assert "device_count=1" in caplog.text
        assert "stage=rooms result=started" in caplog.text
    mock_client.close.assert_awaited_once()
    assert not hass.config_entries.async_entries(DOMAIN)
    diagnostic_text = "\n".join(
        record.getMessage() for record in caplog.records if record.name == LOGGER
    )
    for secret in (
        str(error),
        TEST_DEVICE.id,
        TEST_DEVICE.name,
        TEST_ACCOUNT_FINGERPRINT,
        TEST_CAPTCHA.sc,
        TEST_CAPTCHA_CODE,
        TEST_NATIVE_INPUT["email"],
        TEST_NATIVE_INPUT["password"],
        TEST_CACHED_CREDENTIALS.q,
        TEST_CACHED_CREDENTIALS.t,
        TEST_CACHED_CREDENTIALS.qid,
    ):
        assert secret not in diagnostic_text


@pytest.mark.parametrize("captcha_required", [False, True])
async def test_discovery_success_logs_counts_for_each_device(
    hass, mock_client, caplog, captcha_required
) -> None:
    """Multi-robot diagnostics expose counts and ordinal indices, not identities."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    second_device = replace(
        TEST_DEVICE, id="private-second-id", name="private-second-name"
    )
    mock_client.get_devices.return_value = [TEST_DEVICE, second_device]
    mock_client.get_rooms.side_effect = [TEST_ROOMS, TEST_ROOMS[:1]]
    if captcha_required:
        mock_client.authenticate.side_effect = CaptchaRequired(TEST_CAPTCHA)
    result = await _submit_user_flow(hass)
    if captcha_required:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CAPTCHA_CODE: TEST_CAPTCHA_CODE}
        )
    assert result["step_id"] == "room_areas"
    diagnostics = [
        record.getMessage() for record in caplog.records if record.name == LOGGER
    ]
    assert any(
        "stage=device_list result=succeeded" in line and "device_count=2" in line
        for line in diagnostics
    )
    for index, count in ((1, 2), (2, 1)):
        assert any(
            "stage=rooms result=succeeded" in line
            and f"room_count={count} device_index={index}" in line
            for line in diagnostics
        )
    assert "stage=discovery" not in "\n".join(diagnostics)
    for private_value in (
        TEST_DEVICE.id,
        TEST_DEVICE.name,
        second_device.id,
        second_device.name,
        *(room.name for room in TEST_ROOMS),
    ):
        assert private_value not in "\n".join(diagnostics)
    assert mock_client.get_rooms.await_args_list[0].args == (TEST_DEVICE,)
    assert mock_client.get_rooms.await_args_list[1].args == (second_device,)


def test_diagnostics_never_log_arbitrary_exception_metadata(
    mock_client, caplog
) -> None:
    """Free-form metadata, messages and chained exceptions cannot leak through logging."""

    caplog.set_level(logging.DEBUG, logger=LOGGER)
    secret = "synthetic-sensitive-value"
    error = ApiError(
        secret,
        phase=secret,
        errno=secret,
        status_code=secret,
        response_errno=secret,
        error_code=secret,
    )
    error.__cause__ = ValueError(secret)
    mock_client.auth_backend = secret
    _log_authentication_result(mock_client, "smart_home", "failed", error)
    assert secret not in caplog.text
    assert "backend=unknown" in caplog.text
    assert "phase=unknown" in caplog.text
    assert (
        "errno=None response_errno=None error_code=None http_status=None" in caplog.text
    )
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("errno", [102, 103])
async def test_public_client_preserves_post_quc_session_failure(hass, errno) -> None:
    """The existing public library contract already retains credentials and API metadata."""

    import httpx
    from botslab360 import Botslab360Client

    from .conftest import TEST_DEVICE_IDENTITY

    async def reject_session(request):
        assert str(request.url) == "https://q.smart.360.cn/common/user/login"
        return httpx.Response(200, json={"errno": errno})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_session)) as http:
        client = Botslab360Client.from_credentials(
            email=TEST_NATIVE_INPUT["email"],
            password=TEST_NATIVE_INPUT["password"],
            backend=AuthBackend.ROBOT360,
            device_identity=TEST_DEVICE_IDENTITY,
            http_client=http,
        )
        with (
            patch("botslab360.quc.QucAuth.login", return_value=TEST_CACHED_CREDENTIALS),
            pytest.raises(
                InvalidSessionError if errno == 102 else AuthenticationError
            ) as raised,
        ):
            await client.continue_authentication(TEST_CAPTCHA, TEST_CAPTCHA_CODE)
        assert not isinstance(raised.value, QucAuthenticationError)
        assert raised.value.errno == errno
        assert raised.value.response_errno == errno
        assert raised.value.status_code == 200
        assert raised.value.phase == "api"
        assert client.credentials == TEST_CACHED_CREDENTIALS
        assert client.session is None
        await client.close()
        assert not http.is_closed
