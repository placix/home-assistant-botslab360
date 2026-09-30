"""Sensor platform for Botslab 360."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from botslab360 import Device, RobotStatus, Room

from . import Botslab360ConfigEntry
from .areas import DiscoveredRoom
from .const import DOMAIN
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
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.fan_mode,
    ),
    Botslab360SensorEntityDescription(
        key="raw_state",
        translation_key="raw_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.state,
    ),
    Botslab360SensorEntityDescription(
        key="raw_total_cleaned_area",
        translation_key="raw_total_cleaned_area",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.total_cleaned_area_raw,
    ),
    Botslab360SensorEntityDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.total_cleaning_time_seconds,
    ),
    Botslab360SensorEntityDescription(
        key="raw_sub_state",
        translation_key="raw_sub_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.sub_state,
    ),
    Botslab360SensorEntityDescription(
        key="raw_last_sub_state",
        translation_key="raw_last_sub_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.last_sub_state,
    ),
    Botslab360SensorEntityDescription(
        key="position_x",
        translation_key="position_x",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.position_x,
    ),
    Botslab360SensorEntityDescription(
        key="position_y",
        translation_key="position_y",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.position_y,
    ),
    Botslab360SensorEntityDescription(
        key="heading",
        translation_key="heading",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.heading,
    ),
    Botslab360SensorEntityDescription(
        key="raw_timer_status",
        translation_key="raw_timer_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.timer_status,
    ),
    Botslab360SensorEntityDescription(
        key="raw_auto_boost",
        translation_key="raw_auto_boost",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.auto_boost,
    ),
    Botslab360SensorEntityDescription(
        key="raw_mop_status",
        translation_key="raw_mop_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.mop_status,
    ),
)


@dataclass(frozen=True, kw_only=True)
class Botslab360RoomSensorEntityDescription(SensorEntityDescription):
    """Describe a disabled room diagnostic sensor."""

    value_fn: Callable[[Room], StateType]


ROOM_SENSOR_DESCRIPTIONS = (
    Botslab360RoomSensorEntityDescription(
        key="room_id",
        translation_key="room_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: room.id,
    ),
    Botslab360RoomSensorEntityDescription(
        key="room_type",
        translation_key="room_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: room.room_type,
    ),
    Botslab360RoomSensorEntityDescription(
        key="raw_sweep_area_mode",
        translation_key="raw_sweep_area_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: room.mode,
    ),
    Botslab360RoomSensorEntityDescription(
        key="raw_water_pump",
        translation_key="raw_water_pump",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: room.water_pump,
    ),
    Botslab360RoomSensorEntityDescription(
        key="polygon_vertex_count",
        translation_key="polygon_vertex_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda room: len(room.vertices) if room.vertices is not None else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Botslab360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Botslab 360 sensors."""

    coordinator = entry.runtime_data.coordinator
    robot_sensors = [
        Botslab360Sensor(coordinator, device, description)
        for device in coordinator.devices.values()
        for description in SENSOR_DESCRIPTIONS
    ]
    room_sensors = [
        Botslab360RoomSensor(
            coordinator,
            prepared.discovered_room,
            prepared.parent_device_id,
            description,
        )
        for prepared in entry.runtime_data.rooms
        for description in ROOM_SENSOR_DESCRIPTIONS
    ]
    async_add_entities([*robot_sensors, *room_sensors])


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


class Botslab360RoomSensor(Botslab360Entity, SensorEntity):
    """Representation of a disabled room diagnostic sensor."""

    entity_description: Botslab360RoomSensorEntityDescription

    def __init__(
        self,
        coordinator: Botslab360Coordinator,
        discovered_room: DiscoveredRoom,
        parent_device_id: str,
        description: Botslab360RoomSensorEntityDescription,
    ) -> None:
        """Initialize a room diagnostic sensor."""

        device = discovered_room.device
        room = discovered_room.room
        super().__init__(coordinator, device)
        self.room = room
        self.entity_description = description
        self._attr_unique_id = f"{device.id}_room_{room.id}_{description.key}"
        self._attr_device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, f"{device.id}_room_{room.id}")},
            name=room.name or f"Room {room.id}",
            parent_device_id=parent_device_id,
        )

    @property
    def native_value(self) -> StateType:
        """Return the room diagnostic value."""

        return self.entity_description.value_fn(self.room)
