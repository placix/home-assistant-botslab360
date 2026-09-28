"""Sensor platform for Botslab 360."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from botslab360 import Device, RobotStatus
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import Botslab360ConfigEntry
from .coordinator import Botslab360Coordinator
from .entity import Botslab360Entity


@dataclass(frozen=True, kw_only=True)
class Botslab360SensorEntityDescription(SensorEntityDescription):
    """Describe a Botslab 360 sensor."""

    value_fn: Callable[[RobotStatus], StateType]


SENSOR_DESCRIPTIONS = (
    Botslab360SensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.battery,
    ),
    Botslab360SensorEntityDescription(
        key="cleaned_area",
        translation_key="cleaned_area",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.cleaned_area_m2,
    ),
    Botslab360SensorEntityDescription(
        key="cleaning_time",
        translation_key="cleaning_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.cleaning_time_seconds,
    ),
    Botslab360SensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.error_code,
    ),
    Botslab360SensorEntityDescription(
        key="fan_mode",
        translation_key="fan_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.fan_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Botslab 360 sensors."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        Botslab360Sensor(coordinator, device, description)
        for device in coordinator.devices.values()
        for description in SENSOR_DESCRIPTIONS
    )


class Botslab360Sensor(Botslab360Entity, SensorEntity):
    """Representation of a Botslab 360 status sensor."""

    entity_description: Botslab360SensorEntityDescription

    def __init__(
        self,
        coordinator: Botslab360Coordinator,
        device: Device,
        description: Botslab360SensorEntityDescription,
    ) -> None:
        """Initialize the sensor entity."""

        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the sensor's latest value."""

        if (status := self.robot_status) is None:
            return None
        return self.entity_description.value_fn(status)
