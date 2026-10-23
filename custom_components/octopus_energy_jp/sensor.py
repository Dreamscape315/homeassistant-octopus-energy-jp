"""Sensor platform for Octopus Energy Japan."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_ACCOUNT_NUMBER, DOMAIN
from .coordinator import (
    OctopusJapanDataUpdateCoordinator,
    start_of_local_month,
    start_of_local_week,
)

# Real enum values for Account.status (from introspection); ENUM sensors
# need `options` declared up front.
ACCOUNT_STATUS_OPTIONS = [
    "pending",
    "incomplete",
    "withdrawn",
    "active",
    "enrolment_error",
    "enrolment_rejected",
    "dormant",
    "void",
]

# Real enum values for ElectricitySupplyPoint.status.
SUPPLY_STATUS_OPTIONS = [
    "on_supply",
    "off_supply",
    "switch_in_pending",
    "switch_out_pending",
    "move_in_pending",
    "move_out_pending",
    "move_out_move_in_pending",
    "registration_rejected",
    "lost",
    "cancellation_in_progress_due_to_dunning",
    "cancelled_due_to_dunning",
]

# ContractCapacityUnits enum -> display unit symbol.
_CONTRACT_CAPACITY_UNIT_MAP = {
    "A": "A",
    "KVA": "kVA",
    "KW": "kW",
    "KVA_LESS_THAN": "kVA",
}



async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OctopusJapanDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            OctopusJapanLatestConsumptionSensor(coordinator, entry),
            OctopusJapanLatestCostEstimateSensor(coordinator, entry),
            OctopusJapanTodayConsumptionSensor(coordinator, entry),
            OctopusJapanTodayCostEstimateSensor(coordinator, entry),
            OctopusJapanWeekConsumptionSensor(coordinator, entry),
            OctopusJapanWeekCostEstimateSensor(coordinator, entry),
            OctopusJapanMonthConsumptionSensor(coordinator, entry),
            OctopusJapanMonthCostEstimateSensor(coordinator, entry),
            OctopusJapanTotalConsumptionSensor(coordinator, entry),
            OctopusJapanTotalCostSensor(coordinator, entry),
            OctopusJapanBalanceSensor(coordinator, entry),
            OctopusJapanOverdueBalanceSensor(coordinator, entry),
            OctopusJapanAccountStatusSensor(coordinator, entry),
            OctopusJapanSupplyStatusSensor(coordinator, entry),
            OctopusJapanContractedCapacitySensor(coordinator, entry),
            OctopusJapanTariffPlanSensor(coordinator, entry),
            OctopusJapanStandingChargeSensor(coordinator, entry),
            OctopusJapanFuelCostAdjustmentSensor(coordinator, entry),
            OctopusJapanRenewableEnergyLevySensor(coordinator, entry),
            OctopusJapanLatestBillSensor(coordinator, entry),
        ]
    )


class OctopusJapanBaseSensor(CoordinatorEntity[OctopusJapanDataUpdateCoordinator], SensorEntity):
    """Shared DeviceInfo logic for all sensors.

    Meter serial number/contracted capacity/current tariff plan are
    "what is this device" identity info, so they go into DeviceInfo
    (visible under Settings -> Devices & services -> device details page)
    rather than occupying separate sensor entities. Contracted Capacity /
    Tariff Plan still keep dedicated sensor entities since they carry
    richer info (tiered pricing details, etc.).
    """

    def __init__(
        self, coordinator: OctopusJapanDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._account_number = entry.data[CONF_ACCOUNT_NUMBER]

    @property
    def device_info(self) -> DeviceInfo:
        supply_point = (self.coordinator.data or {}).get("primary_supply_point") or {}
        meters = supply_point.get("meters") or []
        serial_number = meters[0].get("serialNumber") if meters else None

        tariff = supply_point.get("current_tariff") or {}
        model = tariff.get("display_name") or "Kraken API"
        model_id = tariff.get("code")

        capacity = supply_point.get("contractedCapacity") or {}
        hw_version = None
        if capacity.get("value") is not None:
            unit = _CONTRACT_CAPACITY_UNIT_MAP.get(capacity.get("unit"), "")
            hw_version = f"{capacity['value']}{unit}"

        return DeviceInfo(
            identifiers={(DOMAIN, self._account_number)},
            name=f"Octopus Energy Japan ({self._account_number})",
            manufacturer="Octopus Energy",
            model=model,
            model_id=model_id,
            hw_version=hw_version,
            serial_number=serial_number,
        )



def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: str | None, options: list[str]) -> str | None:
    """Lower-case an enum from Kraken; None for values HA wasn't told about
    (an ENUM sensor reporting an unlisted option raises an error)."""
    if not value:
        return None
    value = value.lower()
    return value if value in options else None


class OctopusJapanLatestConsumptionSensor(OctopusJapanBaseSensor):
    """The most recent completed half-hourly consumption reading (kWh).

    No state_class: the value goes up and down every half hour, so neither
    TOTAL nor TOTAL_INCREASING describes it, and recorder's sums for it
    would be meaningless.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "kWh"
    _attr_name = "Latest Half-Hourly Consumption"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_latest_half_hourly_consumption"

    @property
    def native_value(self) -> float | None:
        reading = self.coordinator.data.get("latest_reading")
        if not reading:
            return None
        return _float_or_none(reading.get("value"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self.coordinator.data.get("latest_reading")
        if not reading:
            return {}
        return {
            "start_at": reading.get("startAt"),
            "end_at": reading.get("endAt"),
        }


class OctopusJapanLatestCostEstimateSensor(OctopusJapanBaseSensor):
    """Estimated cost (JPY) corresponding to the latest half-hourly
    consumption. No state_class, same reasoning as the consumption one."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Latest Half-Hourly Cost Estimate"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_latest_half_hourly_cost"

    @property
    def native_value(self) -> float | None:
        reading = self.coordinator.data.get("latest_reading")
        if not reading:
            return None
        return _float_or_none(reading.get("costEstimate"))


class OctopusJapanTodayConsumptionSensor(OctopusJapanBaseSensor):
    """Cumulative consumption for today (local calendar day, midnight to now).

    Due to meter reporting delay, the last hour or two of usage may not be
    reflected yet -- this is normal. device_class=ENERGY only allows
    state_class TOTAL/TOTAL_INCREASING, and this value resets to 0 every
    midnight, so we use TOTAL with `last_reset` pointing at today's
    midnight, telling recorder this is an expected periodic reset.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_name = "Today's Consumption"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_today_consumption"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("today_consumption_kwh", 0.0), 3)

    @property
    def last_reset(self):
        return dt_util.start_of_local_day()


class OctopusJapanTodayCostEstimateSensor(OctopusJapanBaseSensor):
    """Cumulative estimated cost for today (local calendar day); also a
    "so far" partial value that fills in gradually.

    device_class=MONETARY only allows state_class=TOTAL (not even
    TOTAL_INCREASING, let alone MEASUREMENT), same reasoning as above.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Today's Cost Estimate"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_today_cost"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("today_cost_jpy", 0.0), 2)

    @property
    def last_reset(self):
        return dt_util.start_of_local_day()


class OctopusJapanWeekConsumptionSensor(OctopusJapanBaseSensor):
    """Cumulative consumption for this week (Monday 00:00 to now, local time).

    Same reasoning as Today's Consumption: `last_reset` points at this
    Monday 00:00, using TOTAL + last_reset for a periodic reset. Data
    comes straight from HA's own long-term statistics table (see
    `coordinator._async_get_period_total`), no extra cost or truncation
    risk from re-querying Kraken.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_name = "This Week's Consumption"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_week_consumption"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("week_consumption_kwh", 0.0), 3)

    @property
    def last_reset(self):
        return start_of_local_week()


class OctopusJapanWeekCostEstimateSensor(OctopusJapanBaseSensor):
    """Cumulative estimated cost for this week (Monday 00:00 to now)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "This Week's Cost Estimate"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_week_cost"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("week_cost_jpy", 0.0), 2)

    @property
    def last_reset(self):
        return start_of_local_week()


class OctopusJapanMonthConsumptionSensor(OctopusJapanBaseSensor):
    """Cumulative consumption for this month (1st, 00:00 to now, local
    time); same reasoning as This Week's Consumption."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_name = "This Month's Consumption"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_month_consumption"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("month_consumption_kwh", 0.0), 3)

    @property
    def last_reset(self):
        return start_of_local_month()


class OctopusJapanMonthCostEstimateSensor(OctopusJapanBaseSensor):
    """Cumulative estimated cost for this month (1st, 00:00 to now)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "This Month's Cost Estimate"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_month_cost"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.get("month_cost_jpy", 0.0), 2)

    @property
    def last_reset(self):
        return start_of_local_month()


class OctopusJapanTotalConsumptionSensor(OctopusJapanBaseSensor):
    """Cumulative consumption since the contract start (same number as the
    external statistics' latest `sum`, summed across meters).

    For the Energy Dashboard, prefer the external statistic
    `octopus_energy_jp:<account>_<spin>_consumption`: it records each
    hour's usage at the time it happened, whereas recorder's statistics for
    this entity book it at the time the (hours-late) meter data arrived.

    Unknown while the statistics are still catching up (first backfill), so
    recorder takes the full total as its starting point instead of counting
    the backfilled history as new consumption.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"
    _attr_name = "Total Consumption"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_total_consumption"

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get("total_consumption_kwh")
        return round(value, 3) if value is not None else None


class OctopusJapanTotalCostSensor(OctopusJapanBaseSensor):
    """Cumulative estimated cost since the contract start. Same caveats as
    Total Consumption; prefer the `..._cost` external statistic for the
    Energy Dashboard's "track costs".

    Unlike Total Consumption, this can't use TOTAL_INCREASING -- HA
    requires device_class=MONETARY to use state_class=TOTAL only. This
    value only ever increases and never truly "resets", so `last_reset`
    doesn't need to be implemented (defaults to None).
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Total Cost"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_total_cost"

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get("total_cost_jpy")
        return round(value, 2) if value is not None else None


class OctopusJapanBalanceSensor(OctopusJapanBaseSensor):
    """Account balance.

    `Account.balance` is an Int; the Japanese Kraken instance stores plain
    yen -- **no need to divide by 100** (doesn't follow the GBP instance's
    minor-unit convention; verified against a real bill).

    Sign convention hasn't been verified against a real non-zero balance
    yet -- if you find the sign is reversed (e.g. a positive balance shows
    as negative), please report the actual numbers so it can be adjusted.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Account Balance"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_balance"

    @property
    def native_value(self) -> float | None:
        account = self.coordinator.data.get("account")
        if not account or account.get("balance") is None:
            return None
        return float(account["balance"])


class OctopusJapanOverdueBalanceSensor(OctopusJapanBaseSensor):
    """Overdue amount on the account. Same as Account Balance, confirmed
    no need to divide by 100 (see `OctopusJapanBalanceSensor` docstring)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Overdue Balance"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_overdue_balance"

    @property
    def native_value(self) -> float | None:
        account = self.coordinator.data.get("account")
        if not account or account.get("overdueBalance") is None:
            return None
        return float(account["overdueBalance"])


class OctopusJapanAccountStatusSensor(OctopusJapanBaseSensor):
    """Account status (ACTIVE/WITHDRAWN/DORMANT etc., from `Account.status`).

    This is identity/diagnostic info about "what state is this account
    in right now", not a continuously changing reading, so it's
    categorized as Diagnostic, moved from the main entity list to the
    device details page's "Diagnostic" group.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ACCOUNT_STATUS_OPTIONS
    _attr_name = "Account Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_account_status"

    @property
    def native_value(self) -> str | None:
        return _enum_value(self.coordinator.data.get("account_status"), ACCOUNT_STATUS_OPTIONS)


class OctopusJapanSupplyStatusSensor(OctopusJapanBaseSensor):
    """Supply status (ON_SUPPLY/OFF_SUPPLY etc., from
    `ElectricitySupplyPoint.status`). Currently only shows the first meter's
    status.

    Categorized as Diagnostic, same reasoning as Account Status.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SUPPLY_STATUS_OPTIONS
    _attr_name = "Supply Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_supply_status"

    @property
    def native_value(self) -> str | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        if not supply_point:
            return None
        return _enum_value(supply_point.get("status"), SUPPLY_STATUS_OPTIONS)


class OctopusJapanContractedCapacitySensor(OctopusJapanBaseSensor):
    """Contracted capacity (e.g. 30A/40A); unit follows Kraken's
    `ContractCapacityUnits` enum (A/kVA/kW).

    Categorized as Diagnostic: this is static contract configuration, not
    a continuously changing reading.
    """

    _attr_name = "Contracted Capacity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_contracted_capacity"

    @property
    def native_value(self) -> float | None:
        capacity = self._capacity()
        if not capacity or capacity.get("value") is None:
            return None
        try:
            return float(capacity["value"])
        except (TypeError, ValueError):
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        capacity = self._capacity() or {}
        return _CONTRACT_CAPACITY_UNIT_MAP.get(capacity.get("unit"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        supply_point = self.coordinator.data.get("primary_supply_point")
        if not supply_point:
            return {}
        meters = supply_point.get("meters") or []
        meter_capacity = meters[0].get("capacity") if meters else None
        return {
            "amperage": supply_point.get("amperage"),
            "kva": supply_point.get("kva"),
            # The meter hardware's own rated max capacity (different from
            # contracted capacity: contracted capacity is the contractual
            # usage cap, this is the physical meter's rating, usually a
            # bit higher than the contracted capacity).
            "meter_max_capacity_amperes": meter_capacity,
        }

    def _capacity(self) -> dict[str, Any] | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        return supply_point.get("contractedCapacity") if supply_point else None


class OctopusJapanTariffPlanSensor(OctopusJapanBaseSensor):
    """Name of the currently active tariff plan (tiered / flat-rate /
    solar feed-in, from the `Agreement.product` union type, already
    flattened in api.py).

    Categorized as Diagnostic: this is contract configuration, not a
    continuously changing reading.
    """

    _attr_name = "Tariff Plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_tariff_plan"

    @property
    def native_value(self) -> str | None:
        tariff = self._tariff()
        return tariff.get("display_name") if tariff else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tariff = self._tariff()
        if not tariff:
            return {}
        supply_point = self.coordinator.data.get("primary_supply_point") or {}
        return {
            "code": tariff.get("code"),
            "kind": tariff.get("kind"),
            "consumption_tiers": tariff.get("consumption_tiers"),
            "valid_from": supply_point.get("current_agreement_valid_from"),
            "valid_to": supply_point.get("current_agreement_valid_to"),
        }

    def _tariff(self) -> dict[str, Any] | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        return supply_point.get("current_tariff") if supply_point else None


class OctopusJapanStandingChargeSensor(OctopusJapanBaseSensor):
    """Standing charge (JPY/day). This field is already a plain-yen
    Decimal value (like half-hourly readings' costEstimate), no need to
    divide by 100.

    Categorized as Diagnostic: this is tariff configuration (changes with
    the contract, not a real-time usage/cost reading).
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Standing Charge"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_standing_charge"

    @property
    def native_value(self) -> float | None:
        tariff = self._tariff()
        return tariff.get("standing_charge_price_per_day") if tariff else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tariff = self._tariff()
        if not tariff:
            return {}
        return {"unit_type": tariff.get("standing_charge_unit_type")}

    def _tariff(self) -> dict[str, Any] | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        return supply_point.get("current_tariff") if supply_point else None


class OctopusJapanFuelCostAdjustmentSensor(OctopusJapanBaseSensor):
    """Fuel cost adjustment unit price (JPY/kWh). A standard line item on
    Japanese electricity bills, adjusted monthly with fuel prices
    (`valid_from`/`valid_to` attributes show this adjustment's validity
    window).

    Categorized as Diagnostic: this is tariff configuration, not a
    real-time usage/cost reading.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Fuel Cost Adjustment"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_fuel_cost_adjustment"

    @property
    def native_value(self) -> float | None:
        rate = self._rate()
        return rate.get("price_per_unit_inc_tax") if rate else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rate = self._rate()
        if not rate:
            return {}
        return {"valid_from": rate.get("valid_from"), "valid_to": rate.get("valid_to")}

    def _rate(self) -> dict[str, Any] | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        tariff = supply_point.get("current_tariff") if supply_point else None
        return tariff.get("fuel_cost_adjustment") if tariff else None


class OctopusJapanRenewableEnergyLevySensor(OctopusJapanBaseSensor):
    """Renewable energy levy unit price (JPY/kWh). A standard line item on
    Japanese electricity bills, adjusted once a year in April (verified:
    its validity window runs from late April this year to late April next
    year).

    Categorized as Diagnostic: this is tariff configuration, not a
    real-time usage/cost reading.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Renewable Energy Levy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_renewable_energy_levy"

    @property
    def native_value(self) -> float | None:
        rate = self._rate()
        return rate.get("price_per_unit_inc_tax") if rate else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rate = self._rate()
        if not rate:
            return {}
        return {"valid_from": rate.get("valid_from"), "valid_to": rate.get("valid_to")}

    def _rate(self) -> dict[str, Any] | None:
        supply_point = self.coordinator.data.get("primary_supply_point")
        tariff = supply_point.get("current_tariff") if supply_point else None
        return tariff.get("renewable_energy_levy") if tariff else None


class OctopusJapanLatestBillSensor(OctopusJapanBaseSensor):
    """Total amount due (JPY, tax included) for the most recent bill.

    Amount fields (`grossTotal`/`openingBalance`/`closingBalance`) do not
    need to be divided by 100, same reasoning as `OctopusJapanBalanceSensor`.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "JPY"
    _attr_name = "Latest Bill Amount"

    @property
    def unique_id(self) -> str:
        return f"{self._account_number}_latest_bill_amount"

    @property
    def native_value(self) -> float | None:
        bill = self._latest_bill()
        if not bill:
            return None
        gross = (bill.get("totalCharges") or {}).get("grossTotal")
        if gross is None:
            return None
        return float(gross)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bill = self._latest_bill()
        if not bill:
            return {}
        opening_balance = bill.get("openingBalance")
        closing_balance = bill.get("closingBalance")
        return {
            "bill_type": bill.get("billType"),
            "from_date": bill.get("fromDate"),
            "to_date": bill.get("toDate"),
            "issued_date": bill.get("issuedDate"),
            "opening_balance": (
                float(opening_balance) if opening_balance is not None else None
            ),
            "closing_balance": (
                float(closing_balance) if closing_balance is not None else None
            ),
        }

    def _latest_bill(self) -> dict[str, Any] | None:
        bills = self.coordinator.data.get("bills") or []
        return bills[0] if bills else None

