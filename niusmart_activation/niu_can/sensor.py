from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.core import callback

from .const import DOMAIN, SIGNAL_UPDATE


SENSORS = (
    ("state", "Etat", None),
    ("mode", "Mode", None),
    ("fan", "Ventilateur", None),
    (
        "heat_setpoint",
        "Consigne chauffage",
        SensorDeviceClass.TEMPERATURE
    ),
    (
        "cool_setpoint",
        "Consigne climatisation",
        SensorDeviceClass.TEMPERATURE
    ),
)


async def async_setup_entry(
    hass,
    entry,
    async_add_entities
):
    reader = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for zone in (1, 2):
        for key, name, device_class in SENSORS:
            entities.append(
                NiuZoneSensor(
                    reader,
                    zone,
                    key,
                    name,
                    device_class
                )
            )

    async_add_entities(entities)


class NiuZoneSensor(SensorEntity):

    _attr_should_poll = False

    def __init__(
        self,
        reader,
        zone,
        key,
        name,
        device_class
    ):
        self.reader = reader
        self.zone = zone
        self.key = key

        self._attr_name = f"Niu Dometic R{zone} {name}"

        self._attr_unique_id = (
            f"niu_dometic_r{zone}_{key}"
        )

        self._attr_device_class = device_class

        if device_class == SensorDeviceClass.TEMPERATURE:
            self._attr_native_unit_of_measurement = (
                UnitOfTemperature.FAHRENHEIT
            )

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"dometic_r{self.zone}"
                )
            },
            name=f"Niu Dometic R{self.zone}",
            manufacturer="Dometic",
            model="RV-C Thermostat",
        )

    @property
    def available(self):
        data = self.reader.snapshot_zone(self.zone)
        return bool(data)

    @property
    def native_value(self):
        data = self.reader.snapshot_zone(self.zone)
        return data.get(self.key)

    @property
    def extra_state_attributes(self):
        data = self.reader.snapshot_zone(self.zone)

        return {
            "can_id": data.get("can_id"),
            "raw": data.get("raw"),
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE,
                self._handle_update
            )
        )

    @callback
    def _handle_update(self):
        self.async_write_ha_state()
