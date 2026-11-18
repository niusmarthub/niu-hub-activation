from homeassistant.const import Platform

from .const import DOMAIN
from .can_reader import NiuCanReader
import subprocess
import asyncio

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
]


async def async_setup_entry(hass, entry):
    await asyncio.sleep(15)
    for can_if in ("can0", "can1"):
        subprocess.run(["ip", "link", "set", can_if, "down"], check=False)
        subprocess.run(["ip", "link", "set", can_if, "type", "can", "bitrate", "250000", "restart-ms", "100"], check=False)
        subprocess.run(["ip", "link", "set", can_if, "up"], check=False)

    reader = NiuCanReader(hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = reader

    reader.start()

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS
    )

    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS
    )

    if unloaded:
        reader = hass.data[DOMAIN].pop(entry.entry_id)
        reader.stop()

    return unloaded
