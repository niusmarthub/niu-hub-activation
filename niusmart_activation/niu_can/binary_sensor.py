from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.core import callback

from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass,
    entry,
    async_add_entities
):
    reader = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        NiuPowerFailSensor(reader, 1),
        NiuPowerFailSensor(reader, 2),
    ])


class NiuPowerFailSensor(BinarySensorEntity):

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, reader, zone):
        self.reader = reader
        self.zone = zone

        self._attr_name = f"Niu Dometic R{zone} Power Fail"
        self._attr_unique_id = f"niu_dometic_r{zone}_power_fail"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    "dometic_diagnostics"
                )
            },
            name="Niu Dometic Diagnostics",
            manufacturer="Dometic",
            model="RV-C Diagnostics",
        )

    @property
    def available(self):
        state, raw = self.reader.snapshot_power_fail(self.zone)
        return state is not None

    @property
    def is_on(self):
        state, raw = self.reader.snapshot_power_fail(self.zone)
        return state is True

    @property
    def extra_state_attributes(self):
        state, raw = self.reader.snapshot_power_fail(self.zone)

        return {
            "climatiseur": f"R{self.zone}",
            "diagnostic": (
                "Power Fail"
                if state
                else "Aucun defaut actif"
            ),
            "raw": raw,
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

