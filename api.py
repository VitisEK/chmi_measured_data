"""CHMI measured data API client (groundwater + selected surface-water profiles)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp

try:
    from asyncio import timeout as _timeout
except ImportError:  # pragma: no cover
    from async_timeout import timeout as _timeout

from homeassistant.util import dt as dt_util

from .const import (
    CHMI_AIR_QUALITY_GRAPH_URL,
    CHMI_AIR_QUALITY_STATION_DATA_URL,
    CHMI_AIR_QUALITY_SUPPLEMENTARY_GRAPH_URL,
    CHMI_GROUNDWATER_GRAPH_URL,
    CHMI_METEOROLOGICAL_KLIMA_GRAPH_URL,
    CHMI_METEOROLOGICAL_PRECIP_GRAPH_URL,
    CHMI_METEOROLOGICAL_SNOW_DAILY_GRAPH_URL,
    CHMI_METEOROLOGICAL_STATION_DATA_URL,
    CHMI_METEOROLOGICAL_TEMPERATURE_GRAPH_URL,
    CHMI_METEOROLOGICAL_WIND_GRAPH_URL,
    CHMI_STATION_DATA_URL,
    CHMI_STATION_HOST_SUFFIX,
    CHMI_SURFACE_WATER_FLOOD_LIMITS_TABLE_URL,
    CHMI_SURFACE_WATER_FLOW_GRAPH_URL,
    CHMI_SURFACE_WATER_LEVEL_GRAPH_URL,
    CHMI_SURFACE_WATER_MEASURED_TABLE_URL,
    CHMI_SURFACE_WATER_STATION_DATA_URL,
    DEFAULT_MEASUREMENT_PROFILE,
    PROFILE_AIR_QUALITY,
    PROFILE_GROUNDWATER,
    PROFILE_METEOROLOGICAL,
    PROFILE_SURFACE_WATER_FLOW,
    PROFILE_SURFACE_WATER_LEVEL,
)

URL_FAMILY_GROUNDWATER = "groundwater"
URL_FAMILY_SURFACE_WATER = "surface_water"
URL_FAMILY_AIR_QUALITY = "air_quality"
URL_FAMILY_METEOROLOGICAL = "meteorological"

_GROUNDWATER_ID_RE = re.compile(r"/podzemni-vody/(?P<station_id>\d+)(?:[-/]|$)")
_SURFACE_WATER_ID_RE = re.compile(r"/povrchove-vody/(?P<station_id>\d+)(?:[-/]|$)")
_AIR_QUALITY_ID_RE = re.compile(r"/kvality-ovzdusi/(?P<station_id>[a-z0-9]+)(?:[-/]|$)", re.I)
_METEOROLOGICAL_ID_RE = re.compile(r"/meteorologicke/(?P<station_id>[a-z0-9]+)(?:[-/]|$)", re.I)

_PROFILE_CONFIG: dict[str, dict[str, Any]] = {
    PROFILE_GROUNDWATER: {
        "url_family": URL_FAMILY_GROUNDWATER,
        "station_data_url": CHMI_STATION_DATA_URL,
        "graph_url": CHMI_GROUNDWATER_GRAPH_URL,
        "default_measurement_label": None,
        "default_measurement_unit": None,
        "supports_quantiles": True,
    },
    PROFILE_SURFACE_WATER_FLOW: {
        "url_family": URL_FAMILY_SURFACE_WATER,
        "station_data_url": CHMI_SURFACE_WATER_STATION_DATA_URL,
        "graph_url": CHMI_SURFACE_WATER_FLOW_GRAPH_URL,
        "default_measurement_label": "Prutok",
        "default_measurement_unit": "m3/s",
        "supports_quantiles": False,
    },
    PROFILE_SURFACE_WATER_LEVEL: {
        "url_family": URL_FAMILY_SURFACE_WATER,
        "station_data_url": CHMI_SURFACE_WATER_STATION_DATA_URL,
        "graph_url": CHMI_SURFACE_WATER_LEVEL_GRAPH_URL,
        "default_measurement_label": "Vodni stav",
        "default_measurement_unit": "cm",
        "supports_quantiles": False,
    },
    PROFILE_AIR_QUALITY: {
        "url_family": URL_FAMILY_AIR_QUALITY,
        "station_data_url": CHMI_AIR_QUALITY_STATION_DATA_URL,
        "graph_url": CHMI_AIR_QUALITY_GRAPH_URL,
        "default_measurement_label": "PM10 1h",
        "default_measurement_unit": "ug/m^3",
        "supports_quantiles": False,
    },
    PROFILE_METEOROLOGICAL: {
        "url_family": URL_FAMILY_METEOROLOGICAL,
        "station_data_url": CHMI_METEOROLOGICAL_STATION_DATA_URL,
        "graph_url": CHMI_METEOROLOGICAL_SNOW_DAILY_GRAPH_URL,
        "default_measurement_label": "Snih - celkova vyska",
        "default_measurement_unit": "cm",
        "supports_quantiles": False,
    },
}


class ChmiApiError(Exception):
    """Raised when CHMI data cannot be loaded or parsed."""


class ChmiUndergroundWaterApiError(ChmiApiError):
    """Backward-compatible alias exception for older imports."""


class InvalidChmiStationUrl(ChmiApiError):
    """Raised when the provided CHMI URL is invalid."""


class ChmiStationUrlProfileMismatch(InvalidChmiStationUrl):
    """Raised when URL category does not match the selected measurement profile."""


@dataclass(slots=True)
class ChmiStationUrlInfo:
    """Parsed CHMI station URL info."""

    station_id: str
    url_family: str


@dataclass(slots=True)
class ChmiMeasuredSnapshot:
    """Normalized CHMI station snapshot."""

    station_id: str
    station_url: str
    station_family: str
    measurement_profile: str
    station_name: str | None
    station_code: str | None
    measurement_label: str | None
    measurement_unit: str | None
    latest_value: float | None
    latest_value_time: datetime | None
    date_of_last_measure: datetime | None
    description: str | None
    x: str | None
    y: str | None
    z: str | None
    station_metadata: dict[str, Any]
    latest_point: dict[str, Any] | None
    surface_bundle: dict[str, Any] | None = None
    air_quality_bundle: dict[str, Any] | None = None
    meteorological_bundle: dict[str, Any] | None = None

    @property
    def supports_quantiles(self) -> bool:
        """Whether the selected profile contains CHMI quantiles."""
        return bool(_PROFILE_CONFIG.get(self.measurement_profile, {}).get("supports_quantiles"))


# Backward-compatible name used by current integration modules.
ChmiGroundwaterSnapshot = ChmiMeasuredSnapshot


def parse_station_url_info(station_url: str) -> ChmiStationUrlInfo:
    """Parse CHMI station URL and return station id + URL family."""
    parsed = urlparse(station_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise InvalidChmiStationUrl("Unsupported URL scheme")
    if not parsed.netloc or not parsed.netloc.endswith(CHMI_STATION_HOST_SUFFIX):
        raise InvalidChmiStationUrl("URL must point to chmi.cz")

    match = _GROUNDWATER_ID_RE.search(parsed.path)
    if match:
        return ChmiStationUrlInfo(
            station_id=match.group("station_id"),
            url_family=URL_FAMILY_GROUNDWATER,
        )

    match = _SURFACE_WATER_ID_RE.search(parsed.path)
    if match:
        return ChmiStationUrlInfo(
            station_id=match.group("station_id"),
            url_family=URL_FAMILY_SURFACE_WATER,
        )

    match = _AIR_QUALITY_ID_RE.search(parsed.path)
    if match:
        return ChmiStationUrlInfo(
            station_id=match.group("station_id").upper(),
            url_family=URL_FAMILY_AIR_QUALITY,
        )

    match = _METEOROLOGICAL_ID_RE.search(parsed.path)
    if match:
        return ChmiStationUrlInfo(
            station_id=match.group("station_id").upper(),
            url_family=URL_FAMILY_METEOROLOGICAL,
        )

    raise InvalidChmiStationUrl("Could not extract supported station id from URL path")


def extract_station_id_from_url(station_url: str) -> str:
    """Backward-compatible helper returning station id only."""
    return parse_station_url_info(station_url).station_id


class ChmiMeasuredDataApi:
    """Client for CHMI measured-data station endpoints."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

    async def async_fetch_snapshot_from_url(
        self,
        station_url: str,
        measurement_profile: str = DEFAULT_MEASUREMENT_PROFILE,
    ) -> ChmiMeasuredSnapshot:
        """Fetch station data by CHMI station URL and selected profile."""
        parsed = parse_station_url_info(station_url)
        self._validate_profile_against_url_family(measurement_profile, parsed.url_family)
        return await self.async_fetch_snapshot(
            station_id=parsed.station_id,
            station_url=station_url,
            measurement_profile=measurement_profile,
            station_family=parsed.url_family,
        )

    async def async_fetch_snapshot(
        self,
        station_id: str,
        station_url: str | None = None,
        measurement_profile: str = DEFAULT_MEASUREMENT_PROFILE,
        station_family: str | None = None,
    ) -> ChmiMeasuredSnapshot:
        """Fetch station metadata and latest graph value for selected profile."""
        profile = _PROFILE_CONFIG.get(measurement_profile)
        if profile is None:
            raise ChmiApiError(f"Unsupported measurement profile: {measurement_profile}")

        station_family = station_family or str(profile["url_family"])

        station_data = await self._async_get_json(
            str(profile["station_data_url"]).format(station_id=station_id)
        )
        surface_bundle: dict[str, Any] | None = None
        air_quality_bundle: dict[str, Any] | None = None
        meteorological_bundle: dict[str, Any] | None = None

        if station_family == URL_FAMILY_SURFACE_WATER:
            flow_graph_payload = await self._async_get_json(
                CHMI_SURFACE_WATER_FLOW_GRAPH_URL.format(station_id=station_id)
            )
            level_graph_payload = await self._async_get_json(
                CHMI_SURFACE_WATER_LEVEL_GRAPH_URL.format(station_id=station_id)
            )

            flow_points = self._extract_graph_points(flow_graph_payload)
            level_points = self._extract_graph_points(level_graph_payload)
            latest_flow_point = self._extract_latest_measured_point(flow_points)
            latest_level_point = self._extract_latest_measured_point(level_points)
            flow_forecast = self._extract_forecast_series(flow_points)
            level_forecast = self._extract_forecast_series(level_points)

            graph_payload = (
                flow_graph_payload
                if measurement_profile == PROFILE_SURFACE_WATER_FLOW
                else level_graph_payload
            )

            measured_rows = await self._async_fetch_surface_measured_rows(station_id)
            flood_limit_rows = await self._async_fetch_surface_flood_limit_rows(station_id)
            latest_measured_row = self._select_latest_surface_measured_row(measured_rows)

            surface_bundle = self._build_surface_bundle(
                latest_flow_point=latest_flow_point,
                latest_level_point=latest_level_point,
                flow_forecast=flow_forecast,
                level_forecast=level_forecast,
                latest_measured_row=latest_measured_row,
                flood_limit_rows=flood_limit_rows,
            )
        elif station_family == URL_FAMILY_AIR_QUALITY:
            air_quality_graph_raw = await self._async_get_json(
                CHMI_AIR_QUALITY_GRAPH_URL.format(station_id=station_id)
            )
            air_quality_supplementary_raw = await self._async_get_json(
                CHMI_AIR_QUALITY_SUPPLEMENTARY_GRAPH_URL.format(station_id=station_id)
            )
            graph_payload, air_quality_bundle = self._build_air_quality_graph_payload_and_bundle(
                air_quality_graph_raw,
                air_quality_supplementary_raw,
            )
        elif station_family == URL_FAMILY_METEOROLOGICAL:
            try:
                snow_daily_raw = await self._async_get_json(
                    CHMI_METEOROLOGICAL_SNOW_DAILY_GRAPH_URL.format(station_id=station_id)
                )
            except ChmiApiError:
                snow_daily_raw = None
            klima_graph_raw = await self._async_get_json(
                CHMI_METEOROLOGICAL_KLIMA_GRAPH_URL.format(station_id=station_id)
            )
            temp_graph_raw = await self._async_get_json(
                CHMI_METEOROLOGICAL_TEMPERATURE_GRAPH_URL.format(station_id=station_id)
            )
            try:
                wind_graph_raw = await self._async_get_json(
                    CHMI_METEOROLOGICAL_WIND_GRAPH_URL.format(station_id=station_id)
                )
            except ChmiApiError:
                wind_graph_raw = None
            try:
                precip_graph_raw = await self._async_get_json(
                    CHMI_METEOROLOGICAL_PRECIP_GRAPH_URL.format(station_id=station_id)
                )
            except ChmiApiError:
                precip_graph_raw = None

            graph_payload, meteorological_bundle = self._build_meteorological_graph_payload_and_bundle(
                snow_daily_raw=snow_daily_raw,
                klima_graph_raw=klima_graph_raw,
                temp_graph_raw=temp_graph_raw,
                wind_graph_raw=wind_graph_raw,
                precip_graph_raw=precip_graph_raw,
            )
        else:
            graph_payload = await self._async_get_json(
                str(profile["graph_url"]).format(station_id=station_id)
            )

        if not isinstance(station_data, dict):
            raise ChmiApiError("Station metadata response is not a JSON object")
        if not isinstance(graph_payload, dict):
            raise ChmiApiError("Graph response is not a JSON object")

        graph_points = self._extract_graph_points(graph_payload)

        latest_point = self._extract_latest_measured_point(graph_points)
        latest_value = None
        latest_value_time = None
        measurement_label = None

        if latest_point is not None:
            raw_value = latest_point.get("dataY")
            if isinstance(raw_value, (int, float)):
                latest_value = float(raw_value)
            raw_time = latest_point.get("time")
            if isinstance(raw_time, str):
                latest_value_time = dt_util.parse_datetime(raw_time)
                if latest_value_time is not None:
                    latest_value_time = dt_util.as_utc(latest_value_time)
            raw_label = latest_point.get("labelValueY")
            if isinstance(raw_label, str):
                measurement_label = self._safe_str(raw_label)

        if measurement_label is None:
            measurement_label = profile["default_measurement_label"]

        measurement_unit = self._infer_measurement_unit(
            measurement_profile=measurement_profile,
            measurement_label=measurement_label,
            profile_default_unit=profile["default_measurement_unit"],
        )
        if air_quality_bundle is not None:
            measurement_label = self._safe_str(air_quality_bundle.get("primary_label")) or measurement_label
            measurement_unit = self._safe_str(air_quality_bundle.get("primary_unit")) or measurement_unit
        if meteorological_bundle is not None:
            measurement_label = (
                self._safe_str(meteorological_bundle.get("primary_label")) or measurement_label
            )
            measurement_unit = self._safe_str(meteorological_bundle.get("primary_unit")) or measurement_unit

        # Remove large binary payload not useful for HA state.
        station_data.pop("image", None)
        station_data.pop("imageMimeType", None)

        add_info = station_data.get("addInfo")
        if not isinstance(add_info, dict):
            add_info = {}

        station_name = self._extract_station_name(add_info, station_family)
        station_code = self._safe_str(add_info.get("code"))
        description = self._safe_str(station_data.get("description"))
        x = self._safe_str(station_data.get("x"))
        y = self._safe_str(station_data.get("y"))
        z = self._safe_str(station_data.get("z"))

        date_of_last_measure = None
        raw_last_measure = station_data.get("dateOfLastMeasure")
        if isinstance(raw_last_measure, str):
            date_of_last_measure = dt_util.parse_datetime(raw_last_measure)
            if date_of_last_measure is not None:
                date_of_last_measure = dt_util.as_utc(date_of_last_measure)

        return ChmiMeasuredSnapshot(
            station_id=str(station_id),
            station_url=station_url or "",
            station_family=station_family,
            measurement_profile=measurement_profile,
            station_name=station_name,
            station_code=station_code,
            measurement_label=measurement_label,
            measurement_unit=measurement_unit,
            latest_value=latest_value,
            latest_value_time=latest_value_time,
            date_of_last_measure=date_of_last_measure,
            description=description,
            x=x,
            y=y,
            z=z,
            station_metadata=station_data,
            latest_point=latest_point,
            surface_bundle=surface_bundle,
            air_quality_bundle=air_quality_bundle,
            meteorological_bundle=meteorological_bundle,
        )

    async def _async_get_json(self, url: str) -> Any:
        """GET JSON with timeout and normalized errors."""
        try:
            async with _timeout(20):
                async with self._session.get(url, headers=self._headers) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as err:
            raise ChmiApiError(f"Failed to fetch {url}: {err}") from err

    async def _async_post_json(self, url: str, payload: dict[str, Any]) -> Any:
        """POST JSON with timeout and normalized errors."""
        try:
            async with _timeout(20):
                async with self._session.post(url, json=payload, headers=self._headers) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as err:
            raise ChmiApiError(f"Failed to fetch {url}: {err}") from err

    @staticmethod
    def _extract_graph_points(graph_payload: Any) -> list[Any]:
        """Extract graph points list from CHMI graph payload."""
        if not isinstance(graph_payload, dict):
            raise ChmiApiError("Graph response is not a JSON object")
        graph_points = graph_payload.get("data")
        if not isinstance(graph_points, list):
            raise ChmiApiError("Graph response has unexpected format")
        return graph_points

    async def _async_fetch_surface_measured_rows(self, station_id: str) -> list[dict[str, Any]]:
        """Fetch measured rows table for a surface-water station."""
        payload = {
            "filter": {},
            "sort": {"column": "pome", "direction": "asc"},
            "columns": [],
            "paging": {"start": 0, "size": 1000},
            "search": {"columns": ["st", "pome", "h", "q", "th"], "text": ""},
        }
        data = await self._async_post_json(
            CHMI_SURFACE_WATER_MEASURED_TABLE_URL.format(station_id=station_id),
            payload,
        )
        if not isinstance(data, dict):
            return []
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _async_fetch_surface_flood_limit_rows(self, station_id: str) -> list[dict[str, Any]]:
        """Fetch flood/drought limit rows for a surface-water station."""
        payload = {
            "filter": {},
            "sort": {"column": "name", "direction": "asc"},
            "columns": [],
            "paging": {"start": 0, "size": 50},
            "search": {"columns": ["name", "value"], "text": ""},
        }
        data = await self._async_post_json(
            CHMI_SURFACE_WATER_FLOOD_LIMITS_TABLE_URL.format(station_id=station_id),
            payload,
        )
        if not isinstance(data, dict):
            return []
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _select_latest_surface_measured_row(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Return latest row with any measured value h/q/th."""
        for row in reversed(rows):
            if any(isinstance(row.get(key), (int, float)) for key in ("h", "q", "th")):
                return dict(row)
        return dict(rows[-1]) if rows else None

    def _build_surface_bundle(
        self,
        *,
        latest_flow_point: dict[str, Any] | None,
        latest_level_point: dict[str, Any] | None,
        flow_forecast: dict[str, float],
        level_forecast: dict[str, float],
        latest_measured_row: dict[str, Any] | None,
        flood_limit_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build normalized extra values for surface-water stations."""
        status_enum = None
        status_text = None
        measured_time = None
        water_temp = None
        row_h = None
        row_q = None

        if latest_measured_row is not None:
            row_h = self._float_or_none(latest_measured_row.get("h"))
            row_q = self._float_or_none(latest_measured_row.get("q"))
            water_temp = self._float_or_none(latest_measured_row.get("th"))
            raw_time = latest_measured_row.get("pome")
            if isinstance(raw_time, str):
                measured_time = dt_util.parse_datetime(raw_time)
                if measured_time is not None:
                    measured_time = dt_util.as_utc(measured_time)
            status_enum, status_text = self._parse_surface_status_html(latest_measured_row.get("st"))

        flood_limits = self._parse_surface_flood_limits(flood_limit_rows)

        return {
            "flow_value": self._float_or_none(latest_flow_point.get("dataY") if latest_flow_point else None),
            "flow_time": self._parse_utc_dt(latest_flow_point.get("time") if latest_flow_point else None),
            "level_value": self._float_or_none(latest_level_point.get("dataY") if latest_level_point else None),
            "level_time": self._parse_utc_dt(latest_level_point.get("time") if latest_level_point else None),
            "flow_forecast": flow_forecast,
            "level_forecast": level_forecast,
            "level_status_thresholds": latest_level_point.get("dataStav") if latest_level_point else None,
            "flow_status_thresholds": latest_flow_point.get("dataStav") if latest_flow_point else None,
            "table_row_h": row_h,
            "table_row_q": row_q,
            "water_temperature": water_temp,
            "table_measurement_time": measured_time,
            "status_enum": status_enum,
            "status_text": status_text,
            "flood_limits": flood_limits,
        }

    def _build_air_quality_graph_payload_and_bundle(
        self,
        air_quality_graph_payload: Any,
        supplementary_graph_payload: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize air-quality payloads to generic graph + air-quality bundle."""
        if not isinstance(air_quality_graph_payload, dict):
            raise ChmiApiError("Air-quality graph response is not a JSON object")
        raw_points = air_quality_graph_payload.get("dataPoints")
        raw_dial = air_quality_graph_payload.get("dial")
        if not isinstance(raw_points, list) or not isinstance(raw_dial, dict):
            raise ChmiApiError("Air-quality graph response has unexpected format")

        pollutant_meta: dict[str, dict[str, Any]] = {}
        for key, meta in raw_dial.items():
            if isinstance(key, str) and isinstance(meta, dict):
                pollutant_meta[key] = dict(meta)

        pollutant_series: dict[str, dict[str, float]] = {key: {} for key in pollutant_meta}
        normalized_points: list[dict[str, Any]] = []
        latest_values: dict[str, float] = {}
        latest_values_time: datetime | None = None
        latest_values_time_str: str | None = None

        for point in raw_points:
            if not isinstance(point, dict):
                continue
            raw_time = point.get("timestamp")
            raw_values = point.get("values")
            if not isinstance(raw_time, str) or not isinstance(raw_values, dict):
                continue

            parsed_values: dict[str, float] = {}
            for key, raw_value in raw_values.items():
                if not isinstance(key, str):
                    continue
                parsed = self._to_float(raw_value)
                if parsed is None:
                    continue
                parsed_values[key] = parsed
                pollutant_series.setdefault(key, {})[raw_time] = parsed

            point_row: dict[str, Any] = {"time": raw_time}
            point_row.update(parsed_values)
            normalized_points.append(point_row)

            if parsed_values:
                point_dt = self._parse_utc_dt(raw_time)
                if point_dt is not None and (
                    latest_values_time is None or point_dt >= latest_values_time
                ):
                    latest_values = parsed_values
                    latest_values_time = point_dt
                    latest_values_time_str = raw_time

        primary_key = self._select_air_quality_primary_key(latest_values, pollutant_meta)
        primary_label = self._air_quality_label(primary_key, pollutant_meta.get(primary_key, {}))
        primary_unit = self._air_quality_unit(pollutant_meta.get(primary_key, {}))

        # Attach generic dataY/labelValueY so the existing generic parsing path works.
        for point_row in normalized_points:
            point_row["dataY"] = point_row.get(primary_key)
            point_row["labelValueY"] = primary_label

        supp_bundle = self._parse_air_quality_supplementary_graph(supplementary_graph_payload)

        air_quality_bundle = {
            "pollutant_meta": pollutant_meta,
            "pollutants_latest": latest_values,
            "pollutants_time": latest_values_time,
            "pollutants_time_raw": latest_values_time_str,
            "pollutant_series": {k: v for k, v in pollutant_series.items() if v},
            "primary_key": primary_key,
            "primary_label": primary_label,
            "primary_unit": primary_unit,
            **supp_bundle,
        }
        return {"data": normalized_points}, air_quality_bundle

    def _parse_air_quality_supplementary_graph(self, payload: Any) -> dict[str, Any]:
        """Parse supplementary air-quality graph (temp/humidity/radiation)."""
        result: dict[str, Any] = {
            "supplementary_meta": {},
            "supplementary_latest": {},
            "supplementary_latest_times": {},
            "supplementary_series": {},
        }
        if not isinstance(payload, dict):
            return result

        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            result["supplementary_meta"] = {
                key: dict(meta) for key, meta in parameters.items() if isinstance(key, str) and isinstance(meta, dict)
            }

        rows = payload.get("data")
        if not isinstance(rows, list):
            return result

        latest_values: dict[str, float] = {}
        latest_times: dict[str, datetime] = {}
        series: dict[str, dict[str, float]] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_time = row.get("validityTime")
            if not isinstance(raw_time, str):
                continue
            point_dt = self._parse_utc_dt(raw_time)
            for key in ("temp2m", "humidity", "glrd"):
                parsed = self._to_float(row.get(key))
                if parsed is None:
                    continue
                series.setdefault(key, {})[raw_time] = parsed
                if point_dt is not None:
                    if key not in latest_times or point_dt >= latest_times[key]:
                        latest_times[key] = point_dt
                        latest_values[key] = parsed

        result["supplementary_latest"] = latest_values
        result["supplementary_latest_times"] = latest_times
        result["supplementary_series"] = {k: v for k, v in series.items() if v}
        return result

    def _build_meteorological_graph_payload_and_bundle(
        self,
        *,
        snow_daily_raw: Any | None,
        klima_graph_raw: Any,
        temp_graph_raw: Any,
        wind_graph_raw: Any,
        precip_graph_raw: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize meteorological station payloads to generic graph + meteo bundle."""
        snow_daily = (
            self._parse_meteorological_graph_payload(snow_daily_raw)
            if snow_daily_raw is not None
            else None
        )
        klima_10m = self._parse_meteorological_graph_payload(klima_graph_raw)
        temp_10m = self._parse_meteorological_graph_payload(temp_graph_raw)
        wind_10m = (
            self._parse_meteorological_graph_payload(wind_graph_raw) if wind_graph_raw is not None else None
        )
        precip_10m = (
            self._parse_meteorological_graph_payload(precip_graph_raw)
            if precip_graph_raw is not None
            else None
        )

        snow_meta = snow_daily.get("meta", {}) if isinstance(snow_daily, dict) else {}
        snow_has_entity = isinstance(snow_meta, dict) and bool({"SCE", "SVH"} & set(snow_meta.keys()))

        combined_meta: dict[str, dict[str, Any]] = {}
        combined_series: dict[str, dict[str, float]] = {}
        combined_latest: dict[str, float] = {}
        combined_latest_times: dict[str, datetime] = {}

        for parsed in (snow_daily, klima_10m, temp_10m, wind_10m, precip_10m):
            if not isinstance(parsed, dict):
                continue
            meta = parsed.get("meta")
            series = parsed.get("series")
            latest_values = parsed.get("latest_values")
            latest_times = parsed.get("latest_times")
            if isinstance(meta, dict):
                for key, value in meta.items():
                    if isinstance(key, str) and isinstance(value, dict):
                        combined_meta.setdefault(key, value)
            if isinstance(series, dict):
                for key, value in series.items():
                    if isinstance(key, str) and isinstance(value, dict) and value:
                        combined_series.setdefault(key, {}).update(value)
            if isinstance(latest_values, dict) and isinstance(latest_times, dict):
                for key, value in latest_values.items():
                    ts = latest_times.get(key)
                    if not isinstance(key, str) or not isinstance(value, (int, float)):
                        continue
                    if not isinstance(ts, datetime):
                        continue
                    if key not in combined_latest_times or ts >= combined_latest_times[key]:
                        combined_latest[key] = float(value)
                        combined_latest_times[key] = ts

        primary_key = self._select_meteorological_primary_key(
            combined_meta=combined_meta,
            combined_series=combined_series,
            snow_has_entity=snow_has_entity,
        )
        if primary_key is None:
            raise ChmiApiError("Meteorological station does not provide supported measurement data")

        primary_meta = combined_meta.get(primary_key, {})
        primary_label = self._meteorological_label(primary_key, primary_meta)
        primary_unit = self._meteorological_unit(primary_meta)
        primary_series = combined_series.get(primary_key, {})

        generic_points = [
            {
                "time": timestamp,
                "dataY": value,
                "labelValueY": primary_label,
                primary_key: value,
            }
            for timestamp, value in primary_series.items()
        ]

        bundle = {
            "primary_key": primary_key,
            "primary_label": primary_label,
            "primary_unit": primary_unit,
            "snow_has_entity": snow_has_entity,
            "graphs": {
                "snow_daily": snow_daily,
                "klima_10m": klima_10m,
                "temp_10m": temp_10m,
                "wind_10m": wind_10m,
                "precip_10m": precip_10m,
            },
            "parameter_meta": combined_meta,
            "parameter_series": combined_series,
            "latest_values": combined_latest,
            "latest_times": combined_latest_times,
        }
        return {"data": generic_points}, bundle

    @staticmethod
    def _select_meteorological_primary_key(
        *,
        combined_meta: dict[str, dict[str, Any]],
        combined_series: dict[str, dict[str, float]],
        snow_has_entity: bool,
    ) -> str | None:
        """Pick primary meteo parameter; prefer snow when available."""
        preferred_order = [
            "SCE",
            "SVH",
            "T",
            "TPM",
            "H",
            "SRA10M",
            "SSV10M",
            "F",
            "Fmax",
            "D",
            "Dmax",
        ]
        keys_with_series = {
            key
            for key, value in combined_series.items()
            if isinstance(key, str) and isinstance(value, dict) and value
        }
        keys_with_meta = {key for key in combined_meta if isinstance(key, str)}

        if snow_has_entity:
            for key in ("SCE", "SVH"):
                if key in keys_with_series or key in keys_with_meta:
                    return key

        for key in preferred_order:
            if key in keys_with_series:
                return key
        for key in preferred_order:
            if key in keys_with_meta:
                return key

        if keys_with_series:
            return sorted(keys_with_series)[0]
        if keys_with_meta:
            return sorted(keys_with_meta)[0]
        return None

    def _parse_meteorological_graph_payload(self, payload: Any) -> dict[str, Any]:
        """Parse CHMI meteorological graph payload (`dataPoints` + `dial`)."""
        if not isinstance(payload, dict):
            raise ChmiApiError("Meteorological graph response is not a JSON object")
        raw_points = payload.get("dataPoints")
        raw_dial = payload.get("dial")
        if not isinstance(raw_points, list) or not isinstance(raw_dial, dict):
            raise ChmiApiError("Meteorological graph response has unexpected format")

        meta: dict[str, dict[str, Any]] = {}
        for key, value in raw_dial.items():
            if isinstance(key, str) and isinstance(value, dict):
                meta[key] = dict(value)

        series: dict[str, dict[str, float]] = {}
        latest_values: dict[str, float] = {}
        latest_times: dict[str, datetime] = {}

        for point in raw_points:
            if not isinstance(point, dict):
                continue
            raw_time = point.get("timestamp")
            raw_values = point.get("values")
            if not isinstance(raw_time, str) or not isinstance(raw_values, dict):
                continue
            point_dt = self._parse_utc_dt(raw_time)
            for key, raw_value in raw_values.items():
                if not isinstance(key, str):
                    continue
                parsed = self._to_float(raw_value)
                if parsed is None:
                    continue
                series.setdefault(key, {})[raw_time] = parsed
                if point_dt is not None:
                    if key not in latest_times or point_dt >= latest_times[key]:
                        latest_times[key] = point_dt
                        latest_values[key] = parsed

        return {
            "meta": meta,
            "series": {key: value for key, value in series.items() if value},
            "latest_values": latest_values,
            "latest_times": latest_times,
        }

    def _meteorological_label(self, key: str, meta: dict[str, Any]) -> str:
        """Human-readable meteorological parameter label."""
        name = self._safe_str(meta.get("Name"))
        code = self._safe_str(meta.get("Code")) or key
        if name:
            return self._plain_text_html(name)
        return code

    def _meteorological_unit(self, meta: dict[str, Any]) -> str | None:
        """Normalize meteorological unit string from CHMI HTML."""
        unit_html = self._safe_str(meta.get("UnitHTML"))
        if unit_html is None:
            return None
        unit = unit_html
        unit = unit.replace("m.s<sup>-1</sup>", "m/s")
        unit = unit.replace("&micro;", "u")
        unit = re.sub(r"<sup>(.*?)</sup>", r"^\1", unit)
        unit = re.sub(r"<[^>]+>", "", unit)
        unit = unit.replace(" ", "").strip()
        return unit or None

    @staticmethod
    def _plain_text_html(value: str) -> str:
        """Strip basic HTML tags from CHMI labels."""
        return re.sub(r"<[^>]+>", "", value).strip()

    @staticmethod
    def _select_air_quality_primary_key(
        latest_values: dict[str, float],
        pollutant_meta: dict[str, dict[str, Any]],
    ) -> str:
        """Pick a stable primary pollutant for the generic `measurement` sensor."""
        preferred_order = ("PM10_1H", "PM2_5_1H", "NO2_1H", "O3_1H", "SO2_1H", "CO_1H")
        for key in preferred_order:
            if key in latest_values:
                return key
        for key in preferred_order:
            if key in pollutant_meta:
                return key
        if latest_values:
            return next(iter(latest_values))
        if pollutant_meta:
            return next(iter(pollutant_meta))
        return "PM10_1H"

    def _air_quality_label(self, key: str, meta: dict[str, Any]) -> str:
        """Build a human-readable pollutant label like `PM10 (1h)`."""
        code = self._safe_str(meta.get("Code")) or key.split("_", 1)[0]
        code = code.replace("PM2_5", "PM2.5")
        suffix = ""
        if "_1H" in key:
            suffix = " (1h)"
        elif "_24H" in key:
            suffix = " (24h)"
        return f"{code}{suffix}"

    def _air_quality_unit(self, meta: dict[str, Any]) -> str | None:
        """Return CHMI air-quality unit."""
        return self._safe_str(meta.get("UnitASCII")) or self._safe_str(meta.get("UnitUNICODE"))

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """Convert int/float/numeric string to float."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", "."))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_forecast_series(graph_points: list[Any]) -> dict[str, float]:
        """Extract forecast series from CHMI graph points as {iso_time: value}."""
        forecast: dict[str, float] = {}
        for point in graph_points:
            if not isinstance(point, dict):
                continue
            raw_time = point.get("time")
            raw_value = point.get("forecastY")
            if not isinstance(raw_time, str):
                continue
            if not isinstance(raw_value, (int, float)):
                continue
            forecast[raw_time] = float(raw_value)
        return forecast

    @staticmethod
    def _parse_utc_dt(value: Any) -> datetime | None:
        """Parse CHMI UTC timestamp string."""
        if not isinstance(value, str):
            return None
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        """Convert int/float to float; otherwise None."""
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _parse_surface_status_html(html: Any) -> tuple[str | None, str | None]:
        """Parse status enum and label from CHMI status HTML cell."""
        if not isinstance(html, str):
            return None, None
        enum_match = re.search(r'data-enum="([^"]+)"', html)
        status_enum = enum_match.group(1).strip() if enum_match else None
        text_match = re.search(r'<div[^>]*data-enum="[^"]+"[^>]*>([^<]+)</div>', html)
        status_text = text_match.group(1).strip() if text_match else None
        return status_enum or None, status_text or None

    def _parse_surface_flood_limits(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Parse flood/drought limits table rows into normalized values."""
        limits: dict[str, Any] = {}
        for row in rows:
            key = self._surface_flood_limit_key(row.get("name"))
            if key is None:
                continue
            raw_value = row.get("value")
            limits[f"{key}_raw"] = raw_value
            limits[f"{key}_cm"] = self._parse_cm_value(raw_value)
        return limits

    @staticmethod
    def _surface_flood_limit_key(name_html: Any) -> str | None:
        """Map CHMI flood-limit row HTML to a normalized key."""
        if not isinstance(name_html, str):
            return None
        if "OBJECT_SPA_SUCHO" in name_html:
            return "sucho"
        for source, target in (
            ("OBJECT_SPA_1", "spa_1"),
            ("OBJECT_SPA_2", "spa_2"),
            ("OBJECT_SPA_3", "spa_3"),
            ("OBJECT_SPA_4", "spa_4"),
        ):
            if source in name_html:
                return target
        return None

    @staticmethod
    def _parse_cm_value(value: Any) -> float | None:
        """Parse a value like '528 cm' to float."""
        if not isinstance(value, str):
            return None
        match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*cm", value, re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _extract_latest_measured_point(graph_points: list[Any]) -> dict[str, Any] | None:
        """Return the latest graph point that contains a measured value."""
        for point in reversed(graph_points):
            if not isinstance(point, dict):
                continue
            if point.get("dataY") is None:
                continue
            return dict(point)
        return None

    @staticmethod
    def _safe_str(value: Any) -> str | None:
        """Return a stripped string or None."""
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @staticmethod
    def _validate_profile_against_url_family(measurement_profile: str, url_family: str) -> None:
        """Ensure selected profile is compatible with the CHMI URL family."""
        profile = _PROFILE_CONFIG.get(measurement_profile)
        if profile is None:
            raise ChmiApiError(f"Unsupported measurement profile: {measurement_profile}")
        expected_family = str(profile["url_family"])
        if expected_family != url_family:
            raise ChmiStationUrlProfileMismatch(
                f"URL family '{url_family}' does not match profile '{measurement_profile}'"
            )

    def _extract_station_name(self, add_info: dict[str, Any], station_family: str) -> str | None:
        """Build a station display name from CHMI metadata."""
        if station_family == URL_FAMILY_GROUNDWATER:
            return self._safe_str(add_info.get("objName")) or self._safe_str(
                add_info.get("municipalityName")
            )
        if station_family == URL_FAMILY_AIR_QUALITY:
            return self._safe_str(add_info.get("location")) or self._safe_str(add_info.get("code"))
        if station_family == URL_FAMILY_METEOROLOGICAL:
            return self._safe_str(add_info.get("code"))

        # Surface-water station: prefer "<station> (<river>)"
        prf_name = self._safe_str(add_info.get("prfName"))
        river_name = self._safe_str(add_info.get("waterFlowName"))
        if prf_name and river_name and river_name.lower() not in prf_name.lower():
            return f"{prf_name} ({river_name})"
        return prf_name or river_name or self._safe_str(add_info.get("orpName"))

    @staticmethod
    def _infer_measurement_unit(
        *,
        measurement_profile: str,
        measurement_label: str | None,
        profile_default_unit: str | None,
    ) -> str | None:
        """Return measurement unit based on label/profile."""
        label = (measurement_label or "").strip().lower()

        if measurement_profile == PROFILE_GROUNDWATER:
            if "hladina" in label:
                return "m"
            if "vydatnost" in label:
                return "l/s"
            return profile_default_unit

        if measurement_profile == PROFILE_AIR_QUALITY:
            return profile_default_unit

        if measurement_profile == PROFILE_METEOROLOGICAL:
            return profile_default_unit

        return profile_default_unit


# Backward-compatible class name used elsewhere in the integration code.
class ChmiUndergroundWaterApi(ChmiMeasuredDataApi):
    """Backward-compatible alias class."""
