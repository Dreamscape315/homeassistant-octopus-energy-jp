"""Config flow for Octopus Energy Japan."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OctopusJapanApiClient,
    OctopusJapanApiError,
    OctopusJapanAuthError,
    OctopusJapanRateLimitError,
)
from .const import CONF_ACCOUNT_NUMBER, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class OctopusEnergyJapanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the config flow for Octopus Energy Japan."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._password: str | None = None
        self._accounts: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def _async_validate(self, email: str, password: str) -> str | None:
        """Log in and load the accounts; returns an error key, or None."""
        client = OctopusJapanApiClient(async_get_clientsession(self.hass), email, password)
        try:
            await client.async_validate()
            self._accounts = await client.async_get_accounts()
        except OctopusJapanAuthError:
            return "invalid_auth"
        except OctopusJapanRateLimitError:
            return "rate_limited"
        except OctopusJapanApiError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while validating Octopus Energy Japan credentials")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_validate(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            if error is None and not self._accounts:
                error = "no_accounts"
            if error is not None:
                errors["base"] = error
            else:
                self._email = user_input[CONF_EMAIL]
                self._password = user_input[CONF_PASSWORD]
                if len(self._accounts) == 1:
                    return await self._async_create_entry(self._accounts[0]["number"])
                return await self.async_step_account()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user pick which account to set up when the login has
        access to multiple accounts."""
        if user_input is not None:
            return await self._async_create_entry(user_input[CONF_ACCOUNT_NUMBER])

        options = {acc["number"]: acc["number"] for acc in self._accounts}
        schema = vol.Schema({vol.Required(CONF_ACCOUNT_NUMBER): vol.In(options)})
        return self.async_show_form(step_id="account", data_schema=schema)

    async def _async_create_entry(self, account_number: str) -> FlowResult:
        await self.async_set_unique_id(account_number)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Octopus Energy Japan ({account_number})",
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_ACCOUNT_NUMBER: account_number,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Started by Home Assistant when the stored password stops working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._reauth_entry is not None
        entry = self._reauth_entry
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error is None and entry.data[CONF_ACCOUNT_NUMBER] not in {
                acc.get("number") for acc in self._accounts
            }:
                error = "no_accounts"
            if error is not None:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )
