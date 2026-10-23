"""
Octopus Energy Japan (Kraken) GraphQL API client.

All query fields below have been verified against a full introspection of
https://api.oejp-kraken.energy/v1/graphql/; field names, arguments and
return types match the live schema.

Auth: email + password (the same credentials used to log in to
octopusenergy.co.jp).

    obtainKrakenToken(input: {email: "...", password: "..."}) -> token

    Important: introspection only exposes APIKey/organizationSecretKey/
    preSignedKey/refreshToken on `ObtainJSONWebTokenInput` (Kraken
    deliberately hides the email/password fields from introspection), but
    passing email/password directly is accepted and processed by the
    server (approach based on the open-source project caru-ini/octopus-bot).

    All subsequent requests carry an `Authorization: JWT <token>` header
    (note the "JWT " prefix -- not a bare token).

The API sits behind AWS CloudFront. When it decides a client is sending too
many requests it answers with an HTTP 403 *HTML* page (not a GraphQL JSON
error), which has nothing to do with the credentials. All requests go
through one shared gate (`_RequestGate`) that spaces them out and, after
such a block, pauses every request for a cooldown period.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    COOLDOWN_STEPS_SECONDS,
    GRAPHQL_URL,
    MIN_REQUEST_INTERVAL_SECONDS,
    TOKEN_ASSUMED_LIFETIME_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class OctopusJapanApiError(Exception):
    """Generic API error."""


class OctopusJapanAuthError(OctopusJapanApiError):
    """Auth-related error: invalid email/password, expired token, etc."""


class OctopusJapanTransientError(OctopusJapanApiError):
    """Timeout, connection failure or 5xx; worth retrying later."""


class OctopusJapanRateLimitError(OctopusJapanApiError):
    """The API blocked or throttled us (WAF 403 page, HTTP 429), or we are
    still inside the cooldown that followed such a block."""

    def __init__(self, message: str, retry_at: datetime) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class _RequestGate:
    """Serializes and spaces out every API request in this process, and
    holds the cooldown after a block.

    Module-level on purpose: all accounts share the same public IP (a WAF
    block applies to all of them), and the state must survive Home
    Assistant re-creating the client when it retries a failed setup.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._last_request = 0.0
        self._blocked_until: datetime | None = None
        self._cooldown_level = 0

    async def async_wait_turn(self) -> None:
        """Call with `lock` held, right before sending a request."""
        self.raise_if_cooling_down()
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)

    def mark_request_done(self) -> None:
        self._last_request = time.monotonic()

    def raise_if_cooling_down(self) -> None:
        if self._blocked_until and datetime.now(timezone.utc) < self._blocked_until:
            raise OctopusJapanRateLimitError(
                "Octopus Energy API requests are paused until "
                f"{self._blocked_until.isoformat(timespec='seconds')} after being blocked",
                self._blocked_until,
            )

    def enter_cooldown(self) -> datetime:
        delay = COOLDOWN_STEPS_SECONDS[min(self._cooldown_level, len(COOLDOWN_STEPS_SECONDS) - 1)]
        self._cooldown_level += 1
        self._blocked_until = datetime.now(timezone.utc) + timedelta(seconds=delay)
        return self._blocked_until

    def record_success(self) -> None:
        self._cooldown_level = 0
        self._blocked_until = None


_GATE = _RequestGate()


def async_enter_cooldown() -> datetime:
    """Pause all API requests (used after repeated transient failures)."""
    return _GATE.enter_cooldown()


TOKEN_MUTATION = """
mutation krakenTokenAuthentication($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    token
    refreshToken
    refreshExpiresIn
  }
}
"""

VIEWER_ACCOUNTS_QUERY = """
query getViewerAccounts {
  viewer {
    id
    email
    accounts {
      number
      status
      balance
    }
  }
}
"""

ACCOUNT_QUERY = """
query getAccount($accountNumber: String!) {
    account(accountNumber: $accountNumber) {
    number
    status
    # balance / overdueBalance are Int; the Japanese Kraken instance stores
    # plain yen, no need to divide by 100 (see const.py for details).
    balance
    overdueBalance
    properties {
      id
      address
      electricitySupplyPoints {
        id
        spin
        status
        amperage
        kva
        contractedCapacity {
          value
          unit
        }
        meters {
          serialNumber
          capacity
        }
        # Agreement.product is a GraphQL union type; verified that Japan
        # actually returns one of the three below, each with a different
        # field structure:
        #   - ElectricitySteppedProduct: tiered pricing (most common, the
        #     "juryo-dento" style)
        #   - ElectricitySingleStepProduct: flat rate (no tiers)
        #   - ElectricityFitProduct: solar feed-in tariff (FIT), only has a
        #     sell price, no standard standing charge/tiered rates/fuel
        #     cost adjustment/renewable energy levy
        agreements {
          validFrom
          validTo
          product {
            __typename
            ... on ElectricitySteppedProduct {
              code
              displayName
              standingChargePricePerDay
              standingChargeUnitType
              consumptionCharges {
                stepStart
                stepEnd
                pricePerUnitIncTax
                band
              }
              fuelCostAdjustment {
                pricePerUnitIncTax
                validFrom
                validTo
              }
              renewableEnergyLevy {
                pricePerUnitIncTax
                validFrom
                validTo
              }
            }
            ... on ElectricitySingleStepProduct {
              code
              displayName
              standingChargePricePerDay
              standingChargeUnitType
              consumptionCharges {
                pricePerUnitIncTax
                band
              }
              fuelCostAdjustment {
                pricePerUnitIncTax
                validFrom
                validTo
              }
              renewableEnergyLevy {
                pricePerUnitIncTax
                validFrom
                validTo
              }
            }
            ... on ElectricityFitProduct {
              code
              displayName
            }
          }
        }
      }
    }
  }
}
"""

# Account.bills is standard GraphQL Relay pagination (edges/node); verified
# that the real bill node type is PeriodBasedDocumentType (not
# StatementType/InvoiceType seen in introspection, which are likely used by
# other regions or are legacy types).
BILLS_QUERY = """
query getBills($accountNumber: String!, $first: Int!) {
  account(accountNumber: $accountNumber) {
    bills(first: $first) {
      totalCount
      edges {
        node {
          __typename
          ... on PeriodBasedDocumentType {
            id
            billType
            fromDate
            toDate
            issuedDate
            openingBalance
            closingBalance
            totalCharges {
              grossTotal
              netTotal
              taxTotal
            }
            totalCredits {
              grossTotal
              netTotal
              taxTotal
            }
          }
        }
      }
    }
  }
}
"""


ELECTRICITY_HALF_HOURLY_QUERY = """
query getElectricityUsage(
  $accountNumber: String!
  $fromDatetime: DateTime
  $toDatetime: DateTime
) {
  account(accountNumber: $accountNumber) {
    properties {
      electricitySupplyPoints {
        spin
        halfHourlyReadings(fromDatetime: $fromDatetime, toDatetime: $toDatetime) {
          startAt
          endAt
          value
          costEstimate
        }
      }
    }
  }
}
"""


def _dt_to_graphql(dt: datetime) -> str:
    """Convert to the ISO8601 string format accepted by the Kraken GraphQL
    DateTime scalar."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_graphql_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_float(value: Any) -> float | None:
    """Decimal scalars come back as strings in GraphQL JSON (e.g. "19.27");
    convert to float consistently."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_rate(rate: dict[str, Any] | None) -> dict[str, Any] | None:
    """FuelCostAdjustmentRate and RenewableEnergyLevyRate have identical
    fields, so flatten them the same way."""
    if not rate:
        return None
    return {
        "price_per_unit_inc_tax": _to_float(rate.get("pricePerUnitIncTax")),
        "valid_from": rate.get("validFrom"),
        "valid_to": rate.get("validTo"),
    }


def _normalize_electricity_product(product: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flatten the `Agreement.product` union type (one of three tariff
    kinds) into a single structure, so callers don't need to care which
    subtype it is (tiered / flat rate / solar feed-in).
    """
    if not product:
        return None
    return {
        "kind": product.get("__typename"),
        "code": product.get("code"),
        "display_name": product.get("displayName"),
        "standing_charge_price_per_day": _to_float(product.get("standingChargePricePerDay")),
        "standing_charge_unit_type": product.get("standingChargeUnitType"),
        "consumption_tiers": [
            {
                "step_start": tier.get("stepStart"),
                "step_end": tier.get("stepEnd"),
                "price_per_unit_inc_tax": _to_float(tier.get("pricePerUnitIncTax")),
                "band": tier.get("band"),
            }
            for tier in (product.get("consumptionCharges") or [])
        ],
        "fuel_cost_adjustment": _normalize_rate(product.get("fuelCostAdjustment")),
        "renewable_energy_levy": _normalize_rate(product.get("renewableEnergyLevy")),
    }


def _pick_current_agreement(agreements: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the currently active entry from a list of Agreements:
    validFrom <= now and (validTo is empty or validTo > now).
    This should never fail to find one in practice, but falls back to
    "the entry with no validTo" and then "the last entry in the list".
    """
    if not agreements:
        return None
    now = datetime.now(timezone.utc)

    for agreement in agreements:
        valid_from = _parse_graphql_datetime(agreement.get("validFrom"))
        valid_to = _parse_graphql_datetime(agreement.get("validTo"))
        if valid_from and valid_from <= now and (valid_to is None or valid_to > now):
            return agreement

    for agreement in agreements:
        if agreement.get("validTo") is None:
            return agreement

    return agreements[-1]


class OctopusJapanApiClient:
    """Wraps calls to the Octopus Energy Japan Kraken GraphQL API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        if not require_auth:
            return await self._async_post(query, variables, None)

        token = await self._async_ensure_token()
        try:
            return await self._async_post(query, variables, token)
        except OctopusJapanAuthError:
            # The token may have expired early or been revoked: log in again
            # once. If the credentials themselves are wrong, the login raises
            # OctopusJapanAuthError and that propagates.
            _LOGGER.debug("Token rejected, logging in again")
            self._token = None
            return await self._async_post(query, variables, await self._async_ensure_token())

    async def _async_post(
        self, query: str, variables: dict[str, Any] | None, token: str | None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            # Kraken requires the Authorization header format "JWT <token>",
            # not a bare token (verified, see caru-ini/octopus-bot).
            headers["Authorization"] = f"JWT {token}"

        async with _GATE.lock:
            await _GATE.async_wait_turn()
            try:
                async with self._session.post(
                    GRAPHQL_URL,
                    json={"query": query, "variables": variables or {}},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    status = resp.status
                    content_type = resp.content_type
                    body = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise OctopusJapanTransientError(
                    f"Failed to connect to Octopus Energy server: {err!r}"
                ) from err
            finally:
                _GATE.mark_request_done()

        if status == 429 or (status == 403 and content_type != "application/json") or (
            content_type == "text/html"
        ):
            # Not a GraphQL answer: CloudFront/WAF refused the request.
            retry_at = _GATE.enter_cooldown()
            _LOGGER.warning(
                "Octopus Energy API blocked the request (HTTP %s, %s); pausing all requests until %s",
                status,
                content_type,
                retry_at.isoformat(timespec="seconds"),
            )
            _LOGGER.debug("Blocked response body: %s", body[:500])
            raise OctopusJapanRateLimitError(
                f"Blocked by the Octopus Energy API (HTTP {status})", retry_at
            )
        if status >= 500:
            raise OctopusJapanTransientError(f"Octopus Energy server error (HTTP {status})")
        if status in (401, 403):
            raise OctopusJapanAuthError(f"Authentication failed (HTTP {status})")

        try:
            payload = json.loads(body)
        except ValueError as err:
            _LOGGER.debug("Unexpected response body: %s", body[:500])
            raise OctopusJapanApiError(
                f"Unexpected non-JSON response (HTTP {status}, {content_type})"
            ) from err

        _GATE.record_success()

        if "errors" in payload and payload["errors"]:
            err = payload["errors"][0]
            ext = err.get("extensions", {}) or {}
            code = ext.get("errorCode")
            message = ext.get("errorDescription") or err.get("message") or "Unknown error"

            if code == "KT-CT-1138" or "credentials are correct" in message:
                raise OctopusJapanAuthError(f"Invalid email or password: {message}")
            if code in ("KT-CT-1139", "KT-CT-1134", "KT-CT-1135") or "Authentication failed" in message:
                raise OctopusJapanAuthError(f"Login failed: {message}")
            if "Authorization" in message and "header" in message:
                raise OctopusJapanAuthError(f"Missing or invalid Authorization header: {message}")
            if "too many requests" in message.lower() or "rate limit" in message.lower():
                retry_at = _GATE.enter_cooldown()
                raise OctopusJapanRateLimitError(f"{code}: {message}", retry_at)

            raise OctopusJapanApiError(f"{code}: {message}")

        return payload.get("data") or {}

    async def _async_ensure_token(self) -> str:
        if (
            self._token is not None
            and self._token_expires_at is not None
            and datetime.now(timezone.utc) < self._token_expires_at
        ):
            return self._token
        await self._async_login()
        assert self._token is not None
        return self._token

    async def _async_login(self) -> None:
        variables = {"input": {"email": self._email, "password": self._password}}
        data = await self._graphql(TOKEN_MUTATION, variables, require_auth=False)
        result = data.get("obtainKrakenToken")
        if not result or not result.get("token"):
            raise OctopusJapanAuthError("Login failed: unable to obtain a valid token.")

        self._token = result["token"]
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=TOKEN_ASSUMED_LIFETIME_MINUTES
        )

    async def async_validate(self) -> None:
        """Used by config_flow to validate the user-entered email/password."""
        await self._async_login()

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        """Get all account numbers under the logged-in login (usually just one)."""
        data = await self._graphql(VIEWER_ACCOUNTS_QUERY)
        viewer = data.get("viewer") or {}
        return viewer.get("accounts") or []

    async def async_get_account(self, account_number: str) -> dict[str, Any]:
        """Get account basics plus all properties/supply points.

        Each electricitySupplyPoint gets two extra fields attached for
        convenience:
        - `current_tariff`: the currently active entry picked from
          `agreements`, with `product` (a union type) flattened into a
          single structure (see `_normalize_electricity_product`).
        - `current_agreement_valid_from`/`current_agreement_valid_to`:
          the validity window of the current agreement.
        """
        data = await self._graphql(ACCOUNT_QUERY, {"accountNumber": account_number})
        account = data.get("account") or {}
        for prop in account.get("properties") or []:
            for supply_point in prop.get("electricitySupplyPoints") or []:
                agreement = _pick_current_agreement(supply_point.get("agreements") or [])
                supply_point["current_tariff"] = (
                    _normalize_electricity_product(agreement.get("product"))
                    if agreement
                    else None
                )
                supply_point["current_agreement_valid_from"] = (
                    agreement.get("validFrom") if agreement else None
                )
                supply_point["current_agreement_valid_to"] = (
                    agreement.get("validTo") if agreement else None
                )
        return account

    async def async_get_bills(
        self, account_number: str, first: int = 5
    ) -> list[dict[str, Any]]:
        """Get the most recent bills (invoices/statements), newest first
        (Kraken's default order).

        Verified the real node type is `PeriodBasedDocumentType`; fields
        for other types (e.g. CollectiveBillType) are not requested here
        and get skipped.
        """
        data = await self._graphql(
            BILLS_QUERY, {"accountNumber": account_number, "first": first}
        )
        account = data.get("account") or {}
        bills_connection = account.get("bills") or {}
        edges = bills_connection.get("edges") or []
        bills: list[dict[str, Any]] = []
        for edge in edges:
            node = edge.get("node") or {}
            if node.get("__typename") == "PeriodBasedDocumentType":
                bills.append(node)
        return bills

    async def async_get_electricity_half_hourly(
        self,
        account_number: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get half-hourly usage readings (kWh + estimated cost) for each
        electricity meter (spin) within the given time range.

        Returns {spin: [reading, ...]}; grouping by spin is so each meter
        can have its own statistic_id for the Energy Dashboard's long-term
        statistics. If you just want an overall "latest reading", combine
        all spins' readings and take the last one.
        """
        data = await self._graphql(
            ELECTRICITY_HALF_HOURLY_QUERY,
            {
                "accountNumber": account_number,
                "fromDatetime": _dt_to_graphql(from_dt),
                "toDatetime": _dt_to_graphql(to_dt),
            },
        )
        account = data.get("account") or {}
        readings_by_spin: dict[str, list[dict[str, Any]]] = {}
        for prop in account.get("properties") or []:
            for supply_point in prop.get("electricitySupplyPoints") or []:
                spin = supply_point.get("spin")
                if not spin:
                    continue
                readings = supply_point.get("halfHourlyReadings") or []
                bucket = readings_by_spin.setdefault(spin, [])
                bucket.extend(readings)

        for readings in readings_by_spin.values():
            readings.sort(key=lambda r: r.get("startAt") or "")

        return readings_by_spin
