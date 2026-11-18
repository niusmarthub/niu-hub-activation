from homeassistant.components.switch import SwitchEntity
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
        NiuR1PowerSwitch(reader)
    ])


class NiuR1PowerSwitch(SwitchEntity):

    _attr_should_poll = False
    _attr_name = "Niu Dometic R1 Marche Arr?t"
    _attr_unique_id = "niu_dometic_r1_power"

    def __init__(self, reader):
        self.reader = reader

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    "dometic_r1"
                )
            },
            name="Niu Dometic R1",
            manufacturer="Dometic",
            model="RV-C Thermostat",
        )

    @property
    def available(self):
        return bool(
            self.reader.snapshot_zone(1)
        )

    @property
    def is_on(self):
        data = self.reader.snapshot_zone(1)

        if not data:
            return False

        return data.get("state") == "ON"

    @property
    def extra_state_attributes(self):
        data = self.reader.snapshot_zone(1)

        return {
            "mode_reel": data.get("mode"),
            "commande_on": "Ventilation",
            "can_id_commande": "0x19FEF99F",
        }

    async def async_turn_on(self, **kwargs):
        await self.hass.async_add_executor_job(
            self.reader.set_r1_power,
            True
        )

    async def async_turn_off(self, **kwargs):
        await self.hass.async_add_executor_job(
            self.reader.set_r1_power,
            False
        )

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
