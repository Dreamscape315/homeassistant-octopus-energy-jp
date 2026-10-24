"""The Octopus Energy Japan integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OctopusJapanApiClient
from .const import CONF_ACCOUNT_NUMBER, DOMAIN
from .coordinator import OctopusJapanDataUpdateCoordinator, async_clear_external_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries created by older versions.

    1.1 -> 1.2: statistics written before 0.1.2 may start later than the
    contract (early versions backfilled only 30 days) or miss the second
    half of some hours, and later syncs only revisit the last 48 hours.
    Delete them once; the first sync rebuilds them from the contract start
    (and resumes from the last written chunk if interrupted).
    """
    if entry.version > 1:
        return False

    if entry.minor_version < 2:
        cleared = await async_clear_external_statistics(hass, entry.data[CONF_ACCOUNT_NUMBER])
        if cleared:
            _LOGGER.info(
                "Rebuilding Energy Dashboard statistics written by an older version: %s",
                ", ".join(cleared),
            )
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an Octopus Energy Japan integration instance from a config entry."""
    session = async_get_clientsession(hass)
    client = OctopusJapanApiClient(
        session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    account_number = entry.data[CONF_ACCOUNT_NUMBER]

    coordinator = OctopusJapanDataUpdateCoordinator(hass, entry, client, account_number)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

