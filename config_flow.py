"""Config flow for CHMI measured data."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
try:  # HA version compatibility (selector APIs differ across releases)
    from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig
    try:
        from homeassistant.helpers.selector import SelectSelectorMode
    except ImportError:  # pragma: no cover - older/newer HA variations
        SelectSelectorMode = None
except ImportError:  # pragma: no cover - fallback to vol.In radios
    SelectSelector = None
    SelectSelectorConfig = None
    SelectSelectorMode = None

from .api import (
    ChmiApiError,
    ChmiGroundwaterSnapshot,
    ChmiStationUrlProfileMismatch,
    ChmiUndergroundWaterApi,
    InvalidChmiStationUrl,
    URL_FAMILY_AIR_QUALITY,
    URL_FAMILY_GROUNDWATER,
    URL_FAMILY_METEOROLOGICAL,
    URL_FAMILY_SURFACE_WATER,
    parse_station_url_info,
)
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
    MAX_SCAN_INTERVAL_MINUTES,
    MEASUREMENT_PROFILES,
    MIN_SCAN_INTERVAL_MINUTES,
    PROFILE_AIR_QUALITY,
    PROFILE_GROUNDWATER,
    PROFILE_METEOROLOGICAL,
    PROFILE_SURFACE_WATER_FLOW,
    PROFILE_SURFACE_WATER_LEVEL,
    UPDATE_MODE_DAILY_TIME,
    UPDATE_MODE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
_TIME_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
CONF_STATION_FAMILY = "station_family"

_STATION_FAMILY_LABELS = {
    URL_FAMILY_GROUNDWATER: "Groundwater (podzemni vody)",
    URL_FAMILY_SURFACE_WATER: "Surface water (povrchove vody)",
    URL_FAMILY_AIR_QUALITY: "Air quality (kvalita ovzdusi)",
    URL_FAMILY_METEOROLOGICAL: "Meteorological (meteorologicke)",
}
_UPDATE_MODE_LABELS = {
    UPDATE_MODE_DAILY_TIME: "Daily at fixed time (1x denne)",
    UPDATE_MODE_INTERVAL: "Interval (kazdych X minut)",
}


def _scan_interval_validator() -> vol.All:
    return vol.All(
        vol.Coerce(int),
        vol.Range(min=MIN_SCAN_INTERVAL_MINUTES, max=MAX_SCAN_INTERVAL_MINUTES),
    )


def _update_mode_validator() -> vol.In:
    return vol.In(_UPDATE_MODE_LABELS)


def _measurement_profile_validator() -> vol.In:
    return vol.In(list(MEASUREMENT_PROFILES))


def _station_family_validator() -> vol.In:
    return vol.In(list(_STATION_FAMILY_LABELS))


def _update_time_validator(value: str) -> str:
    match = _TIME_RE.match(value.strip())
    if not match:
        raise vol.Invalid("invalid_time")

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise vol.Invalid("invalid_time")
    return f"{hour:02d}:{minute:02d}"


def _station_family_from_profile(measurement_profile: str) -> str | None:
    """Return expected URL family for the selected profile."""
    if measurement_profile == PROFILE_GROUNDWATER:
        return URL_FAMILY_GROUNDWATER
    if measurement_profile in (PROFILE_SURFACE_WATER_FLOW, PROFILE_SURFACE_WATER_LEVEL):
        return URL_FAMILY_SURFACE_WATER
    if measurement_profile == PROFILE_AIR_QUALITY:
        return URL_FAMILY_AIR_QUALITY
    if measurement_profile == PROFILE_METEOROLOGICAL:
        return URL_FAMILY_METEOROLOGICAL
    return None


def _default_profile_for_station_family(station_family: str) -> str:
    """Return canonical internal measurement profile for selected station family."""
    if station_family == URL_FAMILY_SURFACE_WATER:
        # Surface-water entries load both level + flow sensors; use flow as the primary measurement.
        return PROFILE_SURFACE_WATER_FLOW
    if station_family == URL_FAMILY_AIR_QUALITY:
        return PROFILE_AIR_QUALITY
    if station_family == URL_FAMILY_METEOROLOGICAL:
        return PROFILE_METEOROLOGICAL
    return PROFILE_GROUNDWATER


def _station_family_schema_selector():
    """Return dropdown selector when available, otherwise fallback to vol.In radios."""
    if SelectSelector is None or SelectSelectorConfig is None:
        return vol.In(_STATION_FAMILY_LABELS)

    try:
        config_kwargs: dict[str, Any] = {
            "options": [
                {"value": key, "label": label}
                for key, label in _STATION_FAMILY_LABELS.items()
            ]
        }
        config_kwargs["mode"] = (
            SelectSelectorMode.DROPDOWN if SelectSelectorMode is not None else "dropdown"
        )
        return SelectSelector(SelectSelectorConfig(**config_kwargs))
    except Exception:  # pragma: no cover - selector API mismatch fallback
        return vol.In(_STATION_FAMILY_LABELS)


class ChmiUndergroundWaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CHMI measured data."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_station_url: str | None = None
        self._pending_station_snapshot: ChmiGroundwaterSnapshot | None = None
        self._pending_measurement_profile: str = DEFAULT_MEASUREMENT_PROFILE
        self._pending_update_mode: str = DEFAULT_UPDATE_MODE
        self._pending_custom_name: str = ""
        self._pending_update_time: str = DEFAULT_UPDATE_TIME
        self._pending_scan_interval: int = DEFAULT_SCAN_INTERVAL_MINUTES

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Create the options flow."""
        return ChmiUndergroundWaterOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle initial setup (common fields only)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            snapshot, normalized, errors = await self._async_validate_station_form_input(user_input)
            if not errors and snapshot is not None and normalized is not None:
                await self.async_set_unique_id(
                    f"{snapshot.station_id}:{normalized[CONF_MEASUREMENT_PROFILE]}"
                )
                self._abort_if_unique_id_configured()

                self._pending_station_url = normalized[CONF_STATION_URL]
                self._pending_station_snapshot = snapshot
                self._pending_measurement_profile = normalized[CONF_MEASUREMENT_PROFILE]
                self._pending_update_mode = normalized[CONF_UPDATE_MODE]
                self._pending_custom_name = normalized[CONF_NAME]

                if self._pending_update_mode == UPDATE_MODE_DAILY_TIME:
                    return await self.async_step_schedule_daily()
                return await self.async_step_schedule_interval()

        return self.async_show_form(
            step_id="user",
            data_schema=self._station_schema(user_input, include_update_mode=True),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle reconfiguration of station URL/profile/name."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            snapshot, normalized, errors = await self._async_validate_station_form_input(user_input)
            if not errors and snapshot is not None and normalized is not None:
                new_unique_id = f"{snapshot.station_id}:{normalized[CONF_MEASUREMENT_PROFILE]}"

                for existing_entry in self._async_current_entries(include_ignore=False):
                    if existing_entry.entry_id == entry.entry_id:
                        continue
                    if existing_entry.unique_id == new_unique_id:
                        return self.async_abort(reason="already_configured")

                title = normalized[CONF_NAME] or snapshot.station_name or f"CHMI {snapshot.station_id}"
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_unique_id,
                    title=title,
                    data_updates={
                        CONF_STATION_URL: normalized[CONF_STATION_URL],
                        CONF_STATION_ID: snapshot.station_id,
                        CONF_MEASUREMENT_PROFILE: normalized[CONF_MEASUREMENT_PROFILE],
                    },
                )

        defaults = {
            CONF_STATION_URL: str(entry.data.get(CONF_STATION_URL, "")),
            CONF_NAME: entry.title,
            CONF_STATION_FAMILY: _station_family_from_profile(
                str(entry.data.get(CONF_MEASUREMENT_PROFILE, DEFAULT_MEASUREMENT_PROFILE))
            )
            or URL_FAMILY_GROUNDWATER,
        }
        if user_input is not None:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._station_schema(defaults, include_update_mode=False),
            errors=errors,
        )

    async def async_step_schedule_daily(self, user_input: dict[str, Any] | None = None):
        """Configure daily-time schedule."""
        if self._pending_station_snapshot is None or self._pending_station_url is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            raw_update_time = str(user_input[CONF_UPDATE_TIME])
            try:
                self._pending_update_time = _update_time_validator(raw_update_time)
            except vol.Invalid:
                errors[CONF_UPDATE_TIME] = "invalid_time"
            else:
                return self._async_create_entry()

        return self.async_show_form(
            step_id="schedule_daily",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_TIME,
                        default=self._pending_update_time,
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_schedule_interval(self, user_input: dict[str, Any] | None = None):
        """Configure interval schedule."""
        if self._pending_station_snapshot is None or self._pending_station_url is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_scan_interval = int(user_input[CONF_SCAN_INTERVAL])
            return self._async_create_entry()

        return self.async_show_form(
            step_id="schedule_interval",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self._pending_scan_interval,
                    ): _scan_interval_validator(),
                }
            ),
            errors=errors,
        )

    def _async_create_entry(self):
        """Create config entry from pending flow state."""
        snapshot = self._pending_station_snapshot
        station_url = self._pending_station_url
        if snapshot is None or station_url is None:
            return self.async_abort(reason="unknown")

        title = self._pending_custom_name or snapshot.station_name or f"CHMI {snapshot.station_id}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_STATION_URL: station_url,
                CONF_STATION_ID: snapshot.station_id,
                CONF_MEASUREMENT_PROFILE: self._pending_measurement_profile,
                CONF_SCAN_INTERVAL: self._pending_scan_interval,
                CONF_UPDATE_MODE: self._pending_update_mode,
                CONF_UPDATE_TIME: self._pending_update_time,
            },
        )

    async def _async_validate_station_form_input(
        self,
        user_input: dict[str, Any],
    ) -> tuple[ChmiGroundwaterSnapshot | None, dict[str, str] | None, dict[str, str]]:
        """Validate station URL + station type selection and fetch preview snapshot."""
        errors: dict[str, str] = {}

        station_url = str(user_input.get(CONF_STATION_URL, "")).strip()
        custom_name = str(user_input.get(CONF_NAME, "")).strip()
        station_family = str(
            user_input.get(
                CONF_STATION_FAMILY,
                _station_family_from_profile(
                    str(user_input.get(CONF_MEASUREMENT_PROFILE, DEFAULT_MEASUREMENT_PROFILE))
                )
                or URL_FAMILY_GROUNDWATER,
            )
        )
        update_mode = str(user_input.get(CONF_UPDATE_MODE, DEFAULT_UPDATE_MODE))
        measurement_profile = _default_profile_for_station_family(station_family)

        try:
            _station_family_validator()(station_family)
        except vol.Invalid:
            errors[CONF_STATION_FAMILY] = "invalid_station_family"
        try:
            _update_mode_validator()(update_mode)
        except vol.Invalid:
            if CONF_UPDATE_MODE in user_input:
                errors[CONF_UPDATE_MODE] = "invalid_update_mode"
            else:
                update_mode = DEFAULT_UPDATE_MODE

        if station_url and CONF_STATION_URL not in errors:
            try:
                parsed_url = parse_station_url_info(station_url)
            except InvalidChmiStationUrl:
                errors[CONF_STATION_URL] = "invalid_url"
            else:
                if parsed_url.url_family != station_family:
                    errors[CONF_STATION_URL] = "url_type_mismatch"
        elif not station_url:
            errors[CONF_STATION_URL] = "invalid_url"

        if errors:
            return None, None, errors

        api = ChmiUndergroundWaterApi(async_get_clientsession(self.hass))
        try:
            snapshot = await api.async_fetch_snapshot_from_url(
                station_url,
                measurement_profile=measurement_profile,
            )
        except ChmiStationUrlProfileMismatch:
            errors[CONF_STATION_URL] = "url_profile_mismatch"
        except InvalidChmiStationUrl:
            errors[CONF_STATION_URL] = "invalid_url"
        except ChmiApiError as err:
            _LOGGER.debug("CHMI validation failed: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # pragma: no cover - safety net for config flow
            _LOGGER.exception("Unexpected error validating CHMI station URL")
            errors["base"] = "unknown"

        if errors:
            return None, None, errors

        return snapshot, {
            CONF_STATION_URL: station_url,
            CONF_NAME: custom_name,
            CONF_STATION_FAMILY: station_family,
            CONF_MEASUREMENT_PROFILE: measurement_profile,
            CONF_UPDATE_MODE: update_mode,
        }, {}

    @staticmethod
    def _station_schema(
        user_input: dict[str, Any] | None = None,
        *,
        include_update_mode: bool,
    ) -> vol.Schema:
        """Build config/reconfigure schema for station URL and station type selection."""
        user_input = user_input or {}
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_STATION_URL,
                default=user_input.get(CONF_STATION_URL, ""),
            ): str,
            vol.Optional(
                CONF_NAME,
                default=user_input.get(CONF_NAME, ""),
            ): str,
            vol.Required(
                CONF_STATION_FAMILY,
                default=user_input.get(
                    CONF_STATION_FAMILY,
                    _station_family_from_profile(
                        str(user_input.get(CONF_MEASUREMENT_PROFILE, DEFAULT_MEASUREMENT_PROFILE))
                    )
                    or URL_FAMILY_GROUNDWATER,
                ),
            ): _station_family_schema_selector(),
        }
        if include_update_mode:
            schema[
                vol.Required(
                    CONF_UPDATE_MODE,
                    default=user_input.get(CONF_UPDATE_MODE, DEFAULT_UPDATE_MODE),
                )
            ] = _update_mode_validator()
        return vol.Schema(schema)


class ChmiUndergroundWaterOptionsFlow(config_entries.OptionsFlow):
    """Handle CHMI measured data options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._pending_station_url = str(config_entry.data.get(CONF_STATION_URL, ""))
        self._pending_station_id = str(config_entry.data.get(CONF_STATION_ID, ""))
        self._pending_unique_id = config_entry.unique_id
        self._selected_update_mode = str(self._get_value(CONF_UPDATE_MODE, DEFAULT_UPDATE_MODE))

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Select scheduling mode first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._selected_update_mode = str(user_input[CONF_UPDATE_MODE])
            self._pending_station_url = str(user_input.get(CONF_STATION_URL, "")).strip()

            current_station_url = str(self._config_entry.data.get(CONF_STATION_URL, "")).strip()
            current_station_id = str(self._config_entry.data.get(CONF_STATION_ID, ""))
            current_profile = str(
                self._config_entry.data.get(CONF_MEASUREMENT_PROFILE, DEFAULT_MEASUREMENT_PROFILE)
            )

            if not self._pending_station_url:
                errors[CONF_STATION_URL] = "invalid_url"
            elif self._pending_station_url != current_station_url:
                api = ChmiUndergroundWaterApi(async_get_clientsession(self.hass))
                try:
                    snapshot = await api.async_fetch_snapshot_from_url(
                        self._pending_station_url,
                        measurement_profile=current_profile,
                    )
                except ChmiStationUrlProfileMismatch:
                    errors[CONF_STATION_URL] = "url_profile_mismatch"
                except InvalidChmiStationUrl:
                    errors[CONF_STATION_URL] = "invalid_url"
                except ChmiApiError as err:
                    _LOGGER.debug("CHMI options URL validation failed: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception:  # pragma: no cover - safety net
                    _LOGGER.exception("Unexpected error validating CHMI station URL in options")
                    errors["base"] = "unknown"
                else:
                    new_unique_id = f"{snapshot.station_id}:{current_profile}"
                    for entry in self.hass.config_entries.async_entries(DOMAIN):
                        if entry.entry_id == self._config_entry.entry_id:
                            continue
                        if entry.unique_id == new_unique_id:
                            errors["base"] = "already_configured"
                            break
                    if not errors:
                        self._pending_station_id = snapshot.station_id
                        self._pending_unique_id = new_unique_id
            else:
                self._pending_station_id = current_station_id
                self._pending_unique_id = self._config_entry.unique_id

            if not errors:
                if self._selected_update_mode == UPDATE_MODE_DAILY_TIME:
                    return await self.async_step_daily_time()
                return await self.async_step_interval()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STATION_URL,
                        default=self._pending_station_url,
                    ): str,
                    vol.Required(
                        CONF_UPDATE_MODE,
                        default=self._selected_update_mode,
                    ): _update_mode_validator(),
                }
            ),
            errors=errors,
        )

    async def async_step_daily_time(self, user_input: dict[str, Any] | None = None):
        """Configure daily update time in options."""
        errors: dict[str, str] = {}
        current_update_time = str(self._get_value(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME))

        if user_input is not None:
            raw_update_time = str(user_input[CONF_UPDATE_TIME])
            try:
                update_time = _update_time_validator(raw_update_time)
            except vol.Invalid:
                errors[CONF_UPDATE_TIME] = "invalid_time"
            else:
                self._async_apply_pending_station_update()
                return self.async_create_entry(
                    data={
                        CONF_UPDATE_MODE: UPDATE_MODE_DAILY_TIME,
                        CONF_UPDATE_TIME: update_time,
                        CONF_SCAN_INTERVAL: int(
                            self._get_value(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
                        ),
                    }
                )
            current_update_time = raw_update_time

        return self.async_show_form(
            step_id="daily_time",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UPDATE_TIME, default=current_update_time): str,
                }
            ),
            errors=errors,
        )

    async def async_step_interval(self, user_input: dict[str, Any] | None = None):
        """Configure interval update in options."""
        current_scan_interval = int(self._get_value(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))

        if user_input is not None:
            self._async_apply_pending_station_update()
            return self.async_create_entry(
                data={
                    CONF_UPDATE_MODE: UPDATE_MODE_INTERVAL,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_UPDATE_TIME: str(self._get_value(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME)),
                }
            )

        return self.async_show_form(
            step_id="interval",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current_scan_interval,
                    ): _scan_interval_validator(),
                }
            ),
        )

    def _get_value(self, key: str, default: Any) -> Any:
        """Read option value, fallback to entry data."""
        return self._config_entry.options.get(key, self._config_entry.data.get(key, default))

    def _async_apply_pending_station_update(self) -> None:
        """Persist station URL/id updates collected in the options flow."""
        current_station_url = str(self._config_entry.data.get(CONF_STATION_URL, "")).strip()
        current_station_id = str(self._config_entry.data.get(CONF_STATION_ID, ""))
        if (
            self._pending_station_url == current_station_url
            and self._pending_station_id == current_station_id
            and self._pending_unique_id == self._config_entry.unique_id
        ):
            return

        new_data = dict(self._config_entry.data)
        new_data[CONF_STATION_URL] = self._pending_station_url
        new_data[CONF_STATION_ID] = self._pending_station_id

        update_kwargs: dict[str, Any] = {"data": new_data}
        if self._pending_unique_id is not None and self._pending_unique_id != self._config_entry.unique_id:
            update_kwargs["unique_id"] = self._pending_unique_id

        self.hass.config_entries.async_update_entry(self._config_entry, **update_kwargs)
