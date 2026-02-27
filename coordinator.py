"""Data update coordinator for CHMI measured data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ChmiApiError, ChmiUndergroundWaterApi
from .const import (
    CONF_MEASUREMENT_PROFILE,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONF_STATION_URL,
    CONF_UPDATE_MODE,
    CONF_UPDATE_TIME,
    DEFAULT_MEASUREMENT_PROFILE,
    DEFAULT_UPDATE_MODE,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_UPDATE_TIME,
    DOMAIN,
    UPDATE_MODE_DAILY_TIME,
    UPDATE_MODE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
_TIME_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


class ChmiUndergroundWaterCoordinator(DataUpdateCoordinator):
    """Coordinator for CHMI underground-water station data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry
        self.station_id = str(config_entry.data[CONF_STATION_ID])
        self.station_url = str(config_entry.data[CONF_STATION_URL])
        self.measurement_profile = str(
            config_entry.data.get(CONF_MEASUREMENT_PROFILE, DEFAULT_MEASUREMENT_PROFILE)
        )
        self.api = ChmiUndergroundWaterApi(async_get_clientsession(hass))
        self._unsub_refresh: Callable[[], None] | None = None
        self._load_schedule_settings()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.measurement_profile}_{self.station_id}",
            update_interval=None,
        )

    async def _async_update_data(self):
        """Fetch data from CHMI."""
        try:
            return await self.api.async_fetch_snapshot(
                station_id=self.station_id,
                station_url=self.station_url,
                measurement_profile=self.measurement_profile,
            )
        except ChmiApiError as err:
            raise UpdateFailed(str(err)) from err

    def async_start_auto_refresh(self) -> None:
        """Start automatic refresh scheduling based on config."""
        self.async_stop_auto_refresh()

        if self.update_mode == UPDATE_MODE_DAILY_TIME:
            hour, minute = _parse_time_of_day(self.update_time)
            self._unsub_refresh = async_track_time_change(
                self.hass,
                self._async_handle_scheduled_refresh,
                hour=hour,
                minute=minute,
                second=0,
            )
            _LOGGER.debug(
                "CHMI station %s scheduled daily refresh at %02d:%02d",
                self.station_id,
                hour,
                minute,
            )
            return

        self._unsub_refresh = async_track_time_interval(
            self.hass,
            self._async_handle_scheduled_refresh,
            timedelta(minutes=self.scan_interval_minutes),
        )
        _LOGGER.debug(
            "CHMI station %s scheduled interval refresh every %s minutes",
            self.station_id,
            self.scan_interval_minutes,
        )

    def async_stop_auto_refresh(self) -> None:
        """Stop automatic refresh scheduling."""
        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None

    @callback
    def _async_handle_scheduled_refresh(self, _now) -> None:
        """Request refresh when scheduler fires."""
        _LOGGER.debug("CHMI station %s scheduler fired", self.station_id)
        self.hass.async_create_task(self.async_request_refresh())

    def _load_schedule_settings(self) -> None:
        """Load and normalize scheduling settings."""
        raw_update_mode = str(
            self.config_entry.options.get(
                CONF_UPDATE_MODE,
                self.config_entry.data.get(CONF_UPDATE_MODE, DEFAULT_UPDATE_MODE),
            )
        )
        if raw_update_mode not in {UPDATE_MODE_INTERVAL, UPDATE_MODE_DAILY_TIME}:
            raw_update_mode = DEFAULT_UPDATE_MODE
        self.update_mode = raw_update_mode

        self.scan_interval_minutes = int(
            self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES),
            )
        )

        raw_update_time = str(
            self.config_entry.options.get(
                CONF_UPDATE_TIME,
                self.config_entry.data.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME),
            )
        ).strip()
        try:
            hour, minute = _parse_time_of_day(raw_update_time)
        except ValueError:
            _LOGGER.warning(
                "Invalid update_time '%s' for CHMI station %s, using %s",
                raw_update_time,
                self.station_id,
                DEFAULT_UPDATE_TIME,
            )
            hour, minute = _parse_time_of_day(DEFAULT_UPDATE_TIME)
        self.update_time = f"{hour:02d}:{minute:02d}"


def _parse_time_of_day(value: str) -> tuple[int, int]:
    """Parse HH:MM local time string."""
    match = _TIME_RE.match(value.strip())
    if not match:
        raise ValueError("Invalid time format")

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Time out of range")
    return hour, minute
