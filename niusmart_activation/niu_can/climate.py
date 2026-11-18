from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import UnitOfTemperature
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
        NiuR1Climate(reader),
        NiuR2Climate(reader),
    ])


class NiuR1Climate(ClimateEntity):

    _attr_should_poll = False
    _attr_name = "Niu Dometic R1 Thermostat"
    _attr_unique_id = "niu_dometic_r1_climate"

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT

    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.FAN_ONLY,
        HVACMode.HEAT,
        HVACMode.COOL,
    ]

    _attr_supported_features = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
    )

    _attr_preset_modes = [
        "D?shumidification",
    ]

    @property
    def preset_mode(self):
        data = self.reader.snapshot_zone(1)

        if data.get("mode") == "D?shumidification":
            return "D?shumidification"

        return None

    async def async_set_preset_mode(self, preset_mode):
        if preset_mode != "D?shumidification":
            raise ValueError(
                f"Preset non supporte: {preset_mode}"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r1_mode,
            "dehumidify"
        )

    _attr_min_temp = 60.0
    _attr_max_temp = 90.0
    _attr_target_temperature_step = 1.0

    _attr_fan_modes = [
        "AUTO",
        "Vitesse 1",
        "Vitesse 2",
        "Vitesse 3",
    ]

    @property
    def fan_mode(self):
        data = self.reader.snapshot_zone(1)

        if not data:
            return None

        return data.get("fan")

    async def async_set_fan_mode(self, fan_mode):
        await self.hass.async_add_executor_job(
            self.reader.set_r1_fan_mode,
            fan_mode
        )

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
    def hvac_mode(self):
        data = self.reader.snapshot_zone(1)

        mode = data.get("mode")

        if data.get("state") == "OFF":
            return HVACMode.OFF

        return {
            "Ventilation": HVACMode.FAN_ONLY,
            "Chauffage": HVACMode.HEAT,
            "Climatisation": HVACMode.COOL,
        }.get(
            mode,
            HVACMode.OFF
        )

    @property
    def target_temperature(self):
        data = self.reader.snapshot_zone(1)

        mode = self.hvac_mode

        if mode == HVACMode.HEAT:
            return data.get("heat_setpoint")

        if mode == HVACMode.COOL:
            return data.get("cool_setpoint")

        return None

    @property
    def extra_state_attributes(self):
        data = self.reader.snapshot_zone(1)

        return {
            "etat_reel": data.get("state"),
            "mode_reel": data.get("mode"),
            "ventilateur": data.get("fan"),
            "consigne_chauffage_f": data.get("heat_setpoint"),
            "consigne_climatisation_f": data.get("cool_setpoint"),
            "can_id": data.get("can_id"),
        }

    async def async_set_temperature(
        self,
        **kwargs
    ):
        temperature = kwargs.get("temperature")

        if temperature is None:
            return

        if self.hvac_mode not in (
            HVACMode.HEAT,
            HVACMode.COOL,
        ):
            raise ValueError(
                "Consigne disponible seulement "
                "en Chauffage ou Climatisation"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r1_temperature,
            temperature
        )

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            mode = "off"

        elif hvac_mode == HVACMode.FAN_ONLY:
            mode = "fan_only"

        elif hvac_mode == HVACMode.HEAT:
            mode = "heat"

        elif hvac_mode == HVACMode.COOL:
            mode = "cool"

        else:
            raise ValueError(
                f"Mode HVAC non supporte: {hvac_mode}"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r1_mode,
            mode
        )

    async def async_turn_on(self):
        await self.hass.async_add_executor_job(
            self.reader.set_r1_mode,
            "fan_only"
        )

    async def async_turn_off(self):
        await self.hass.async_add_executor_job(
            self.reader.set_r1_mode,
            "off"
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


class NiuR2Climate(ClimateEntity):

    _attr_should_poll = False
    _attr_name = "Niu Dometic R2 Thermostat"
    _attr_unique_id = "niu_dometic_r2_climate"

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT

    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.FAN_ONLY,
        HVACMode.HEAT,
        HVACMode.COOL,
    ]

    _attr_supported_features = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
    )

    _attr_preset_modes = [
        "D?shumidification",
    ]

    @property
    def preset_mode(self):
        data = self.reader.snapshot_zone(2)

        if data.get("mode") == "D?shumidification":
            return "D?shumidification"

        return None

    async def async_set_preset_mode(self, preset_mode):
        if preset_mode != "D?shumidification":
            raise ValueError(
                f"Preset non supporte: {preset_mode}"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r2_mode,
            "dehumidify"
        )

    _attr_min_temp = 60.0
    _attr_max_temp = 90.0
    _attr_target_temperature_step = 1.0

    _attr_fan_modes = [
        "AUTO",
        "Vitesse 1",
        "Vitesse 2",
        "Vitesse 3",
    ]

    @property
    def fan_mode(self):
        data = self.reader.snapshot_zone(2)

        if not data:
            return None

        return data.get("fan")

    async def async_set_fan_mode(self, fan_mode):
        await self.hass.async_add_executor_job(
            self.reader.set_r2_fan_mode,
            fan_mode
        )

    def __init__(self, reader):
        self.reader = reader

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    "dometic_r2"
                )
            },
            name="Niu Dometic R2",
            manufacturer="Dometic",
            model="RV-C Thermostat",
        )

    @property
    def available(self):
        return bool(
            self.reader.snapshot_zone(2)
        )

    @property
    def hvac_mode(self):
        data = self.reader.snapshot_zone(2)

        mode = data.get("mode")

        if data.get("state") == "OFF":
            return HVACMode.OFF

        return {
            "Ventilation": HVACMode.FAN_ONLY,
            "Chauffage": HVACMode.HEAT,
            "Climatisation": HVACMode.COOL,
        }.get(
            mode,
            HVACMode.OFF
        )

    @property
    def target_temperature(self):
        data = self.reader.snapshot_zone(2)

        mode = self.hvac_mode

        if mode == HVACMode.HEAT:
            return data.get("heat_setpoint")

        if mode == HVACMode.COOL:
            return data.get("cool_setpoint")

        return None

    @property
    def extra_state_attributes(self):
        data = self.reader.snapshot_zone(2)

        return {
            "etat_reel": data.get("state"),
            "mode_reel": data.get("mode"),
            "ventilateur": data.get("fan"),
            "consigne_chauffage_f": data.get("heat_setpoint"),
            "consigne_climatisation_f": data.get("cool_setpoint"),
            "can_id": data.get("can_id"),
        }

    async def async_set_temperature(
        self,
        **kwargs
    ):
        temperature = kwargs.get("temperature")

        if temperature is None:
            return

        if self.hvac_mode not in (
            HVACMode.HEAT,
            HVACMode.COOL,
        ):
            raise ValueError(
                "Consigne disponible seulement "
                "en Chauffage ou Climatisation"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r2_temperature,
            temperature
        )

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            mode = "off"

        elif hvac_mode == HVACMode.FAN_ONLY:
            mode = "fan_only"

        elif hvac_mode == HVACMode.HEAT:
            mode = "heat"

        elif hvac_mode == HVACMode.COOL:
            mode = "cool"

        else:
            raise ValueError(
                f"Mode HVAC non supporte: {hvac_mode}"
            )

        await self.hass.async_add_executor_job(
            self.reader.set_r2_mode,
            mode
        )

    async def async_turn_on(self):
        await self.hass.async_add_executor_job(
            self.reader.set_r2_mode,
            "fan_only"
        )

    async def async_turn_off(self):
        await self.hass.async_add_executor_job(
            self.reader.set_r2_mode,
            "off"
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

