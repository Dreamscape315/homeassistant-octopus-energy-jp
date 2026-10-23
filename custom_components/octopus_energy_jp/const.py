"""Constants for the Octopus Energy Japan integration."""

DOMAIN = "octopus_energy_jp"

CONF_ACCOUNT_NUMBER = "account_number"

# Kraken JWT tokens are typically valid for 60 minutes; refresh a bit early
# to be safe.
TOKEN_ASSUMED_LIFETIME_MINUTES = 55

DEFAULT_SCAN_INTERVAL_MINUTES = 30

# Window of half-hourly readings fetched on every refresh, used for the
# "latest reading" and "today" sensors. Kraken's smart meter data reporting
# is delayed, so a short window often returns nothing; 3 days covers the
# delay. When the statistics are up to date, the statistics sync reuses
# these readings instead of making a request of its own.
LATEST_READING_LOOKBACK_HOURS = 72

# Account details (tariff, contract) and bills change rarely, so they are
# fetched far less often than the readings.
ACCOUNT_REFRESH_HOURS = 6
BILLS_REFRESH_HOURS = 12

# Note: Account.balance/overdueBalance and bill amounts do NOT need to be
# divided by 100. The Japanese Kraken instance stores plain yen values,
# unlike the GBP instance's minor-unit convention (verified against a real
# bill: ¥5787 paid, API returned 5787).

# History backfill for the Energy Dashboard statistics starts at the
# earliest agreement's validFrom (the contract start; verified that readings
# exist from exactly that moment), but never further back than this.
BACKFILL_MAX_DAYS = 5 * 365

# `halfHourlyReadings` returns at most ~31 days (1488 readings) per query
# and silently drops the OLDEST part of a longer window (verified: a 62-day
# query returned only the most recent 31 days; 7/14/31-day chunks returned
# identical data with no duplicates or gaps at the boundaries). 28 days
# stays safely under that limit.
BACKFILL_CHUNK_DAYS = 28

# Pause between backfill requests, so a long history is fetched slowly in
# the background instead of in a burst that the API's WAF may block.
BACKFILL_REQUEST_SPACING_SECONDS = 15

# Every statistics sync re-fetches and rewrites this many hours before the
# last written hour, picking up late meter data and completing hours that
# were only partly available last time.
STATISTICS_OVERLAP_HOURS = 48

# While the statistics are further behind than this (first setup, or a long
# outage), the Total Consumption/Cost sensors report unknown instead of a
# partial value, so recorder's own entity statistics don't count the
# backfilled history as new consumption.
STATISTICS_CATCHUP_THRESHOLD_DAYS = 7

# If a backfill chunk's first reading starts this much later than requested,
# the response may have been truncated; log a warning.
TRUNCATION_WARNING_HOURS = 24

# Minimum gap between any two API requests, across all accounts (they share
# one public IP, and a WAF block applies to all of them).
MIN_REQUEST_INTERVAL_SECONDS = 2

# After the API blocks us (CloudFront/WAF 403 page, HTTP 429), every request
# is paused for these durations in turn; a successful response resets it.
COOLDOWN_STEPS_SECONDS = (5 * 60, 15 * 60, 60 * 60, 6 * 60 * 60)

# Backfill retries a chunk after timeouts/5xx with these delays, then gives
# up and enters a cooldown.
TRANSIENT_RETRY_DELAYS_SECONDS = (30, 2 * 60, 5 * 60)

# Fetch this many recent bills (invoices/statements) alongside each account
# refresh, for display purposes.
BILLS_FETCH_COUNT = 5

GRAPHQL_URL = "https://api.oejp-kraken.energy/v1/graphql/"
