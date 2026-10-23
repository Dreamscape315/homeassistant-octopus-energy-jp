"""DataUpdateCoordinator for Octopus Energy Japan.

Two jobs run side by side:

- The coordinator refresh (every 30 minutes) feeds the sensors: the last
  72 hours of half-hourly readings (latest reading / today), plus account
  details and bills, which are only re-fetched every few hours.
- A background statistics sync aggregates half-hourly readings into hourly
  buckets and writes them to Home Assistant's long-term statistics as
  external statistics (`octopus_energy_jp:<account>_<spin>_consumption` /
  `_cost`), so they can be used as the grid consumption / cost source under
  Settings -> Dashboards -> Energy. External statistics carry the real
  time of each reading, which matters because the meter data arrives hours
  late. The approach follows the `opower` integration bundled with HA core.

The first sync backfills from the contract start (capped at
BACKFILL_MAX_DAYS) in BACKFILL_CHUNK_DAYS chunks, one request every
BACKFILL_REQUEST_SPACING_SECONDS, writing each chunk as soon as it arrives
so an interrupted backfill resumes where it stopped. Later syncs re-fetch
the last STATISTICS_OVERLAP_HOURS and rewrite them, picking up late data.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

try:
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # Fallback for older HA versions without StatisticMeanType
    StatisticMeanType = None  # type: ignore[assignment,misc]

_MEAN_TYPE_NONE = StatisticMeanType.NONE if StatisticMeanType is not None else None

from .api import (
    OctopusJapanApiClient,
    OctopusJapanApiError,
    OctopusJapanAuthError,
    OctopusJapanRateLimitError,
    OctopusJapanTransientError,
    _parse_graphql_datetime,
    async_enter_cooldown,
)
from .const import (
    ACCOUNT_REFRESH_HOURS,
    BACKFILL_CHUNK_DAYS,
    BACKFILL_MAX_DAYS,
    BACKFILL_REQUEST_SPACING_SECONDS,
    BILLS_FETCH_COUNT,
    BILLS_REFRESH_HOURS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LATEST_READING_LOOKBACK_HOURS,
    STATISTICS_CATCHUP_THRESHOLD_DAYS,
    STATISTICS_OVERLAP_HOURS,
    TRANSIENT_RETRY_DELAYS_SECONDS,
    TRUNCATION_WARNING_HOURS,
)

_LOGGER = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9_]+")

# {spin: {hour_start: {"value": kWh, "cost": JPY}}}
HourlyBuckets = dict[str, dict[datetime, dict[str, float]]]


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("_", value.lower()).strip("_")


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def start_of_local_week(reference: datetime | None = None) -> datetime:
    """Start of "this week" in the local timezone (Monday 00:00).

    `sensor.py` uses this same function for the `last_reset` of This
    Week's Consumption/Cost, so the boundary matches exactly what the
    coordinator uses when querying long-term statistics.
    """
    today = dt_util.start_of_local_day(reference)
    return today - timedelta(days=today.weekday())


def start_of_local_month(reference: datetime | None = None) -> datetime:
    """Start of "this month" in the local timezone (the 1st, 00:00)."""
    today = dt_util.start_of_local_day(reference)
    return today.replace(day=1)


def _aggregate_hourly(
    readings_by_spin: dict[str, list[dict[str, Any]]], since: datetime | None = None
) -> HourlyBuckets:
    """Sum half-hourly readings into hourly {value, cost} buckets per spin,
    skipping readings that start before `since`."""
    buckets: HourlyBuckets = {}
    for spin, readings in readings_by_spin.items():
        hourly = buckets.setdefault(spin, {})
        for reading in readings:
            reading_dt = _parse_graphql_datetime(reading.get("startAt"))
            if reading_dt is None or (since is not None and reading_dt < since):
                continue
            bucket = hourly.setdefault(_floor_hour(reading_dt), {"value": 0.0, "cost": 0.0})
            bucket["value"] += float(reading.get("value") or 0)
            bucket["cost"] += float(reading.get("costEstimate") or 0)
    return buckets


class OctopusJapanDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Feeds the sensors and keeps the Energy Dashboard statistics in sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OctopusJapanApiClient,
        account_number: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Octopus Energy Japan",
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
        )
        self._entry = entry
        self._client = client
        self.account_number = account_number
        self._account_slug = _slugify(account_number)

        self._account: dict[str, Any] | None = None
        self._account_fetched_at: datetime | None = None
        self._bills: list[dict[str, Any]] = []
        self._bills_fetched_at: datetime | None = None
        self._recent_readings: dict[str, list[dict[str, Any]]] = {}
        self._recent_start: datetime | None = None
        self._sync_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Coordinator refresh
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        try:
            await self._async_refresh_account(now)
            await self._async_refresh_bills(now)
            recent_start = _floor_hour(now - timedelta(hours=LATEST_READING_LOOKBACK_HOURS))
            readings_by_spin = await self._client.async_get_electricity_half_hourly(
                self.account_number, recent_start, now
            )
        except OctopusJapanAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OctopusJapanApiError as err:
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            # Keep showing the last known values rather than going
            # unavailable; a Total sensor dropping out and coming back is
            # harmless, but it must never report 0.
            _LOGGER.warning("Refresh failed, keeping the previous values: %s", err)
            return self.data

        self._recent_readings = readings_by_spin
        self._recent_start = recent_start
        self._async_start_statistics_sync()
        return await self._async_build_data()

    async def _async_refresh_account(self, now: datetime) -> None:
        if (
            self._account is not None
            and self._account_fetched_at is not None
            and now - self._account_fetched_at < timedelta(hours=ACCOUNT_REFRESH_HOURS)
        ):
            return
        self._account = await self._client.async_get_account(self.account_number)
        self._account_fetched_at = now

    async def _async_refresh_bills(self, now: datetime) -> None:
        if (
            self._bills_fetched_at is not None
            and now - self._bills_fetched_at < timedelta(hours=BILLS_REFRESH_HOURS)
        ):
            return
        try:
            self._bills = await self._client.async_get_bills(
                self.account_number, first=BILLS_FETCH_COUNT
            )
            self._bills_fetched_at = now
        except (OctopusJapanAuthError, OctopusJapanRateLimitError):
            raise
        except OctopusJapanApiError as err:
            # A bills API failure shouldn't fail the whole refresh; keep the
            # previous bills (if any) and try again next refresh.
            _LOGGER.warning("Failed to fetch bills: %s", err)

    async def _async_build_data(self) -> dict[str, Any]:
        account = self._account or {}
        all_readings = [r for readings in self._recent_readings.values() for r in readings]
        all_readings.sort(key=lambda r: r.get("startAt") or "")
        latest_reading = all_readings[-1] if all_readings else None

        today_start = dt_util.start_of_local_day()
        today_consumption_kwh = 0.0
        today_cost_jpy = 0.0
        for reading in all_readings:
            reading_dt = _parse_graphql_datetime(reading.get("startAt"))
            if reading_dt is not None and reading_dt >= today_start:
                today_consumption_kwh += float(reading.get("value") or 0)
                today_cost_jpy += float(reading.get("costEstimate") or 0)

        # This Week's / This Month's usage and cost come straight from HA's
        # own long-term statistics table (kept up to date by the statistics
        # sync), so they cost no extra API requests.
        spins = self._get_spins(account)
        consumption_ids = {self._statistic_id(spin, "consumption") for spin in spins}
        cost_ids = {self._statistic_id(spin, "cost") for spin in spins}
        week_start = start_of_local_week()
        month_start = start_of_local_month()

        total_consumption_kwh, total_cost_jpy = await self._async_get_totals(spins)

        return {
            "account": account,
            "account_status": account.get("status"),
            "primary_supply_point": self._primary_supply_point(account),
            "bills": self._bills,
            "latest_reading": latest_reading,
            "today_consumption_kwh": today_consumption_kwh,
            "today_cost_jpy": today_cost_jpy,
            "week_consumption_kwh": await self._async_get_period_total(consumption_ids, week_start),
            "week_cost_jpy": await self._async_get_period_total(cost_ids, week_start),
            "month_consumption_kwh": await self._async_get_period_total(consumption_ids, month_start),
            "month_cost_jpy": await self._async_get_period_total(cost_ids, month_start),
            "total_consumption_kwh": total_consumption_kwh,
            "total_cost_jpy": total_cost_jpy,
        }

    def _primary_supply_point(self, account: dict[str, Any]) -> dict[str, Any] | None:
        """Take the first electricitySupplyPoint of the first property as
        the "primary meter".

        Only verified against a real account with a single property/single
        meter; for multi-property/multi-meter cases this only shows the
        first one, matching the known limitation noted in the README.
        """
        for prop in account.get("properties") or []:
            for supply_point in prop.get("electricitySupplyPoints") or []:
                return supply_point
        return None

    def _get_spins(self, account: dict[str, Any]) -> list[str]:
        spins: list[str] = []
        for prop in account.get("properties") or []:
            for sp in prop.get("electricitySupplyPoints") or []:
                spin = sp.get("spin")
                if spin and spin not in spins:
                    spins.append(spin)
        return spins

    def _contract_start(self, account: dict[str, Any]) -> datetime | None:
        """Earliest validFrom across all agreements of all supply points."""
        starts = [
            dt
            for prop in account.get("properties") or []
            for sp in prop.get("electricitySupplyPoints") or []
            for agreement in sp.get("agreements") or []
            if (dt := _parse_graphql_datetime(agreement.get("validFrom"))) is not None
        ]
        return min(starts) if starts else None

    def _statistic_id(self, spin: str, kind: str) -> str:
        return f"{DOMAIN}:{self._account_slug}_{_slugify(spin)}_{kind}"

    # ------------------------------------------------------------------
    # Statistics sync (background)
    # ------------------------------------------------------------------

    def _async_start_statistics_sync(self) -> None:
        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = self._entry.async_create_background_task(
            self.hass,
            self._async_sync_statistics(),
            name=f"{DOMAIN} statistics sync {self.account_number}",
        )

    async def _async_sync_statistics(self) -> None:
        try:
            if await self._async_run_statistics_sync():
                # Publish the new week/month/total values right away instead
                # of waiting for the next refresh.
                await get_instance(self.hass).async_block_till_done()
                self.async_set_updated_data(await self._async_build_data())
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while syncing Energy Dashboard statistics")

    async def _async_run_statistics_sync(self) -> bool:
        """Bring the external statistics up to date. Returns True when it
        reached the present, False when it stopped early (it will resume
        from the last written chunk on the next refresh)."""
        account = self._account or {}
        spins = self._get_spins(account)
        if not spins:
            return False

        now = dt_util.utcnow()
        backfill_floor = now - timedelta(days=BACKFILL_MAX_DAYS)
        contract_start = self._contract_start(account)
        if contract_start is None:
            _LOGGER.warning(
                "No contract start date found; backfilling at most %s days", BACKFILL_MAX_DAYS
            )
            contract_start = backfill_floor
        backfill_start = _floor_hour(max(contract_start, backfill_floor))

        # Make sure everything queued by a previous sync is committed before
        # reading back where we left off.
        await get_instance(self.hass).async_block_till_done()

        start = now
        for spin in spins:
            last_end = await self._async_get_last_end(self._statistic_id(spin, "consumption"))
            if last_end is None:
                spin_start = backfill_start
            else:
                spin_start = max(last_end - timedelta(hours=STATISTICS_OVERLAP_HOURS), backfill_start)
            start = min(start, _floor_hour(spin_start))

        # Running sums at `start` per spin, so rows from `start` onwards can
        # be (re)written with a consistent cumulative sum.
        sums: dict[str, dict[str, float]] = {}
        for spin in spins:
            sums[spin] = {
                "value": await self._async_get_sum_before(self._statistic_id(spin, "consumption"), start),
                "cost": await self._async_get_sum_before(self._statistic_id(spin, "cost"), start),
            }

        if now - start > timedelta(days=STATISTICS_CATCHUP_THRESHOLD_DAYS):
            _LOGGER.info(
                "Backfilling Energy Dashboard statistics from %s (%s-day chunks, one request every %ss)",
                start.isoformat(),
                BACKFILL_CHUNK_DAYS,
                BACKFILL_REQUEST_SPACING_SECONDS,
            )

        chunk_start = start
        requests_made = 0
        while chunk_start < now:
            chunk_end = min(chunk_start + timedelta(days=BACKFILL_CHUNK_DAYS), now)
            if self._recent_start is not None and chunk_start >= self._recent_start and chunk_end == now:
                # The refresh already fetched this window; no extra request.
                readings_by_spin = self._recent_readings
            else:
                if requests_made:
                    await asyncio.sleep(BACKFILL_REQUEST_SPACING_SECONDS)
                readings_by_spin = await self._async_fetch_chunk(chunk_start, chunk_end)
                requests_made += 1
                if readings_by_spin is None:
                    return False

            hourly = _aggregate_hourly(readings_by_spin, since=chunk_start)
            for spin in spins:
                self._write_statistics(spin, hourly.get(spin, {}), sums[spin])
            chunk_start = chunk_end

        _LOGGER.debug("Statistics sync finished (%s request(s))", requests_made)
        return True

    async def _async_fetch_chunk(
        self, chunk_start: datetime, chunk_end: datetime
    ) -> dict[str, list[dict[str, Any]]] | None:
        """Fetch one chunk, retrying timeouts/5xx. Returns None when the sync
        should stop (blocked, auth failure, or retries exhausted)."""
        for delay in (*TRANSIENT_RETRY_DELAYS_SECONDS, None):
            try:
                readings_by_spin = await self._client.async_get_electricity_half_hourly(
                    self.account_number, chunk_start, chunk_end
                )
            except OctopusJapanTransientError as err:
                if delay is None:
                    retry_at = async_enter_cooldown()
                    _LOGGER.warning(
                        "Statistics sync giving up after repeated errors (%s); pausing requests until %s",
                        err,
                        retry_at.isoformat(timespec="seconds"),
                    )
                    return None
                _LOGGER.warning("Statistics sync request failed (%s), retrying in %ss", err, delay)
                await asyncio.sleep(delay)
                continue
            except OctopusJapanRateLimitError as err:
                _LOGGER.warning("Statistics sync paused: %s", err)
                return None
            except OctopusJapanApiError as err:
                _LOGGER.warning("Statistics sync stopped: %s", err)
                return None
            break

        self._warn_if_truncated(readings_by_spin, chunk_start, chunk_end)
        return readings_by_spin

    def _warn_if_truncated(
        self,
        readings_by_spin: dict[str, list[dict[str, Any]]],
        chunk_start: datetime,
        chunk_end: datetime,
    ) -> None:
        """`halfHourlyReadings` drops the oldest part of a too-long window
        without any error; flag a chunk whose data starts much later than
        requested."""
        if chunk_end - chunk_start <= timedelta(hours=TRUNCATION_WARNING_HOURS):
            return
        starts = [
            dt
            for readings in readings_by_spin.values()
            for r in readings
            if (dt := _parse_graphql_datetime(r.get("startAt"))) is not None
        ]
        if starts and min(starts) - chunk_start > timedelta(hours=TRUNCATION_WARNING_HOURS):
            _LOGGER.warning(
                "Readings for %s -> %s only start at %s; the API may have truncated the response",
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                min(starts).isoformat(),
            )

    def _build_metadata(self, statistic_id: str, name: str, unit: str) -> StatisticMetaData:
        metadata: dict[str, Any] = {
            "has_sum": True,
            "name": name,
            "source": DOMAIN,
            "statistic_id": statistic_id,
            "unit_class": None,
            "unit_of_measurement": unit,
        }
        if _MEAN_TYPE_NONE is not None:
            metadata["mean_type"] = _MEAN_TYPE_NONE
        else:
            metadata["has_mean"] = False
        return metadata  # type: ignore[return-value]

    def _write_statistics(
        self, spin: str, hourly: dict[datetime, dict[str, float]], sums: dict[str, float]
    ) -> None:
        """Write one chunk's hourly rows for a spin, advancing `sums` in
        place. Existing rows with the same start hour are overwritten."""
        if not hourly:
            return
        consumption_stats: list[StatisticData] = []
        cost_stats: list[StatisticData] = []
        for hour_start in sorted(hourly):
            bucket = hourly[hour_start]
            sums["value"] += bucket["value"]
            sums["cost"] += bucket["cost"]
            consumption_stats.append(
                StatisticData(start=hour_start, state=bucket["value"], sum=sums["value"])
            )
            cost_stats.append(StatisticData(start=hour_start, state=bucket["cost"], sum=sums["cost"]))

        consumption_id = self._statistic_id(spin, "consumption")
        cost_id = self._statistic_id(spin, "cost")
        _LOGGER.debug(
            "Writing %s hourly statistics for %s (%s -> %s)",
            len(consumption_stats),
            spin,
            consumption_stats[0]["start"].isoformat(),
            consumption_stats[-1]["start"].isoformat(),
        )
        async_add_external_statistics(
            self.hass,
            self._build_metadata(consumption_id, f"Octopus Energy Japan {spin} Consumption", "kWh"),
            consumption_stats,
        )
        async_add_external_statistics(
            self.hass,
            self._build_metadata(cost_id, f"Octopus Energy Japan {spin} Cost", "JPY"),
            cost_stats,
        )

    # ------------------------------------------------------------------
    # Reading back statistics
    # ------------------------------------------------------------------

    async def _async_get_last_row(self, statistic_id: str) -> dict[str, Any] | None:
        last_stat = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        rows = last_stat.get(statistic_id) if last_stat else None
        return rows[0] if rows else None

    async def _async_get_last_end(self, statistic_id: str) -> datetime | None:
        row = await self._async_get_last_row(statistic_id)
        return dt_util.utc_from_timestamp(row["end"]) if row else None

    async def _async_get_sum_before(self, statistic_id: str, start: datetime) -> float:
        """Cumulative sum just before `start`: the latest sum minus the
        hourly deltas (`state`) of every row from `start` onwards."""
        row = await self._async_get_last_row(statistic_id)
        if row is None:
            return 0.0
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            None,
            {statistic_id},
            "hour",
            None,
            {"state"},
        )
        later = sum(float(r.get("state") or 0) for r in rows.get(statistic_id, []))
        return float(row.get("sum") or 0.0) - later

    async def _async_get_totals(self, spins: list[str]) -> tuple[float | None, float | None]:
        """Total consumption/cost across all spins, from the statistics'
        latest cumulative sums. None while the statistics are still
        catching up, so recorder doesn't count backfilled history as new
        consumption on the Total sensors."""
        if not spins:
            return None, None
        catchup_limit = dt_util.utcnow() - timedelta(days=STATISTICS_CATCHUP_THRESHOLD_DAYS)
        consumption = 0.0
        cost = 0.0
        for spin in spins:
            consumption_row = await self._async_get_last_row(self._statistic_id(spin, "consumption"))
            cost_row = await self._async_get_last_row(self._statistic_id(spin, "cost"))
            if consumption_row is None or cost_row is None:
                return None, None
            if dt_util.utc_from_timestamp(consumption_row["end"]) < catchup_limit:
                return None, None
            consumption += float(consumption_row.get("sum") or 0.0)
            cost += float(cost_row.get("sum") or 0.0)
        return consumption, cost

    async def _async_get_period_total(
        self, statistic_ids: set[str], start: datetime
    ) -> float:
        """Sum the hourly deltas from `start` to now (across all given
        statistic_ids, e.g. multiple meters), reading directly from HA's
        own long-term statistics table.

        The `state` field of each row is that hour's delta (not the
        cumulative `sum`), so summing it directly works.
        """
        if not statistic_ids:
            return 0.0

        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            None,
            statistic_ids,
            "hour",
            None,
            {"state"},
        )
        total = 0.0
        for stat_rows in rows.values():
            for row in stat_rows:
                total += float(row.get("state") or 0)
        return total
