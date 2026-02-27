"""Sensor platform for CHMI measured data."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChmiGroundwaterSnapshot
from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    PROFILE_AIR_QUALITY,
    PROFILE_GROUNDWATER,
    PROFILE_METEOROLOGICAL,
    PROFILE_SURFACE_WATER_FLOW,
    PROFILE_SURFACE_WATER_LEVEL,
)
from .coordinator import ChmiUndergroundWaterCoordinator

ATTRIBUTION = "Data source: CHMI (data-provider.chmi.cz)"
QUANTILE_KEYS = ("q5", "q15", "q25", "q50", "q75", "q85", "q95")
SURFACE_PROFILES = (PROFILE_SURFACE_WATER_FLOW, PROFILE_SURFACE_WATER_LEVEL)
METEOROLOGICAL_PROFILES = (PROFILE_METEOROLOGICAL,)
METEOROLOGICAL_PARAMETER_ORDER = (
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
)
METEOROLOGICAL_PARAM_OVERRIDES: dict[str, dict[str, Any]] = {
    "SCE": {"suffix": "snow_height", "name": "Snih", "icon": "mdi:snowflake"},
    "SVH": {"suffix": "snow_water_equivalent", "name": "Vodni hodnota snehu", "icon": "mdi:water"},
    "T": {
        "suffix": "air_temperature",
        "name": "Teplota vzduchu",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
    },
    "TPM": {
        "suffix": "ground_temperature",
        "name": "Prizemni teplota",
        "icon": "mdi:thermometer-lines",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
    },
    "H": {
        "suffix": "air_humidity",
        "name": "Relativni vlhkost",
        "icon": "mdi:water-percent",
        "device_class": SensorDeviceClass.HUMIDITY,
        "unit": "%",
    },
    "SRA10M": {"suffix": "precipitation_10m", "name": "Srazky (10 min)", "icon": "mdi:weather-rainy"},
    "SSV10M": {"suffix": "sunshine_10m", "name": "Slunecni svit (10 min)", "icon": "mdi:white-balance-sunny"},
    "F": {"suffix": "wind_speed", "name": "Rychlost vetru", "icon": "mdi:weather-windy"},
    "Fmax": {"suffix": "wind_gust", "name": "Naraz vetru", "icon": "mdi:weather-windy-variant"},
    "D": {"suffix": "wind_direction", "name": "Smer vetru", "icon": "mdi:compass"},
    "Dmax": {"suffix": "wind_gust_direction", "name": "Smer narazu vetru", "icon": "mdi:compass-outline"},
}
AIR_QUALITY_POLLUTANT_ORDER = ("PM10_1H", "PM2_5_1H", "NO2_1H", "O3_1H", "SO2_1H", "CO_1H")
AIR_QUALITY_SUPPLEMENTARY_META = {
    "temp2m": {"suffix": "air_temp_2m", "name": "Teplota vzduchu", "unit": UnitOfTemperature.CELSIUS},
    "humidity": {"suffix": "air_humidity", "name": "Relativni vlhkost", "unit": "%"},
    "glrd": {"suffix": "global_radiation", "name": "Globalni zareni", "unit": "W/m^2"},
}
SURFACE_LIMIT_KEYS = ("spa_1", "spa_2", "spa_3", "spa_4", "sucho")
OBJ_TYPE_LABELS_CS = {
    "MELKY_VRT": "Melky vrt",
    "HLUBOKY_VRT": "Hluboky vrt",
    "PRAMEN": "Pramen",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CHMI measured data sensors from config entry."""
    coordinator: ChmiUndergroundWaterCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    snapshot = coordinator.data
    supports_quantiles = bool(snapshot and snapshot.supports_quantiles)

    entities: list[SensorEntity] = [ChmiUndergroundWaterMeasurementSensor(coordinator, entry)]

    if coordinator.measurement_profile in SURFACE_PROFILES:
        entities.extend(_build_surface_water_entities(coordinator, entry))
    elif coordinator.measurement_profile in METEOROLOGICAL_PROFILES:
        entities.extend(_build_meteorological_entities(coordinator, entry))
    elif coordinator.measurement_profile == PROFILE_AIR_QUALITY:
        entities.extend(_build_air_quality_entities(coordinator, entry))

    if supports_quantiles:
        entities.append(ChmiUndergroundWaterStatusSensor(coordinator, entry))
        entities.extend(
            [
                ChmiUndergroundWaterQuantileSensor(coordinator, entry, quantile_key)
                for quantile_key in QUANTILE_KEYS
            ]
        )

    entities.append(ChmiUndergroundWaterLastMeasurementSensor(coordinator, entry))
    async_add_entities(entities)


class ChmiUndergroundWaterBaseEntity(CoordinatorEntity, SensorEntity):
    """Base entity for CHMI measured data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.measurement_profile}_{coordinator.station_id}_{suffix}"

    @property
    def _snapshot(self) -> ChmiGroundwaterSnapshot | None:
        return self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        """Return device metadata."""
        snapshot = self._snapshot
        station_name = snapshot.station_name if snapshot else self._entry.title
        station_metadata = snapshot.station_metadata if snapshot else {}
        add_info = station_metadata.get("addInfo", {}) if isinstance(station_metadata, dict) else {}
        if not isinstance(add_info, dict):
            add_info = {}

        obj_type = add_info.get("objType")
        if not isinstance(obj_type, str):
            obj_type = None
        model = _device_model(snapshot, obj_type)

        station_code = add_info.get("code")
        if not isinstance(station_code, str):
            station_code = None

        region = add_info.get("regionName")
        if not isinstance(region, str):
            region = None

        municipality = add_info.get("municipalityName")
        if not isinstance(municipality, str):
            municipality = None
        orp_name = add_info.get("orpName")
        if not isinstance(orp_name, str):
            orp_name = None

        measure_point = add_info.get("measurePoint")
        if not isinstance(measure_point, str):
            measure_point = None

        kwargs = {
            "identifiers": {(DOMAIN, _device_identifier(self.coordinator, snapshot))},
            "name": _device_name(station_name, station_code, self.coordinator.station_id),
            "manufacturer": "CHMI",
            "model": model,
            "sw_version": station_code,
            "serial_number": f"CHMI-{self.coordinator.station_id}",
            "hw_version": measure_point,
            "suggested_area": municipality
            or orp_name
            or _air_quality_location(snapshot)
            or _air_quality_district(snapshot)
            or region,
            "configuration_url": self.coordinator.station_url,
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        return _build_device_info_compat(kwargs)


class ChmiUndergroundWaterMeasurementSensor(ChmiUndergroundWaterBaseEntity):
    """Primary measured value from CHMI graph data."""

    _attr_translation_key = "measurement"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "measurement")

    @property
    def name(self) -> str | None:
        """Use CHMI label for surface-water profiles to avoid generic 'Measurement'."""
        snapshot = self._snapshot
        if (
            snapshot
            and snapshot.measurement_profile in (*SURFACE_PROFILES, PROFILE_AIR_QUALITY, *METEOROLOGICAL_PROFILES)
            and snapshot.measurement_label
        ):
            return snapshot.measurement_label
        return None

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.latest_value

    @property
    def native_unit_of_measurement(self) -> str | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.measurement_unit

    @property
    def icon(self) -> str:
        snapshot = self._snapshot
        if snapshot is None:
            return "mdi:waves"
        if snapshot.measurement_profile == PROFILE_SURFACE_WATER_FLOW:
            return "mdi:waves-arrow-right"
        if snapshot.measurement_profile == PROFILE_SURFACE_WATER_LEVEL:
            return "mdi:gauge"
        if snapshot.measurement_profile == PROFILE_AIR_QUALITY:
            return "mdi:air-filter"
        if snapshot.measurement_profile == PROFILE_METEOROLOGICAL:
            meteo_bundle = _meteorological_bundle(snapshot) or {}
            primary_key = meteo_bundle.get("primary_key")
            if primary_key in {"SCE", "SVH"}:
                return "mdi:snowflake"
            if primary_key in {"T", "TPM"}:
                return "mdi:thermometer"
            if primary_key == "H":
                return "mdi:water-percent"
            if primary_key in {"F", "Fmax"}:
                return "mdi:weather-windy"
            if primary_key == "SRA10M":
                return "mdi:weather-rainy"
            if primary_key == "SSV10M":
                return "mdi:weather-sunny"
            return "mdi:weather-partly-cloudy"
        label = (snapshot.measurement_label or "") or ""
        label_lower = label.lower()
        if "hladina" in label_lower:
            return "mdi:waves-arrow-up"
        if "vydatnost" in label_lower:
            return "mdi:waves-arrow-right"
        return "mdi:waves"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None

        station_metadata = snapshot.station_metadata
        add_info = station_metadata.get("addInfo", {}) if isinstance(station_metadata, dict) else {}
        if not isinstance(add_info, dict):
            add_info = {}

        attrs: dict[str, Any] = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "station_id": snapshot.station_id,
            "station_family": getattr(snapshot, "station_family", None),
            "measurement_profile": getattr(snapshot, "measurement_profile", None),
            "station_code": snapshot.station_code,
            "measurement_label": snapshot.measurement_label,
            "measurement_time": _dt_to_iso(snapshot.latest_value_time),
            "date_of_last_measure": _dt_to_iso(snapshot.date_of_last_measure),
            "source_url": self.coordinator.station_url,
            "station_detail_url": add_info.get("dataUrl"),
            "description": snapshot.description,
            "coordinates_x": snapshot.x,
            "coordinates_y": snapshot.y,
            "elevation": snapshot.z,
            "object_type": add_info.get("objType"),
            "station_category_code": add_info.get("category"),
            "municipality": add_info.get("municipalityName"),
            "district": add_info.get("districtName"),
            "orp": add_info.get("orpName"),
            "river": add_info.get("waterFlowName"),
            "cadastral_territory": add_info.get("cadastralTerritoryName"),
            "region": add_info.get("regionName"),
            "hydrogeological_region": add_info.get("hgrName"),
            "department": add_info.get("chmiDepName"),
            "measure_point": add_info.get("measurePoint"),
            "station_owner": add_info.get("locationOwner"),
            "air_quality_classification": add_info.get("classifcation"),
            "eoi_station_type": add_info.get("eoiStationType"),
            "eoi_zone_type": add_info.get("eoiZoneType"),
            "eoi_zone_characteristic": add_info.get("eoiZoneCharacteristic"),
            "eoi_sub_characteristic": add_info.get("eoiSubCharacteristic"),
            "station_web_url": add_info.get("linkToWeb"),
        }

        latest_point = snapshot.latest_point or {}
        if "dataStav" in latest_point and isinstance(latest_point["dataStav"], list):
            attrs["water_status_thresholds"] = latest_point["dataStav"]
        for key in ("q5", "q15", "q25", "q50", "q75", "q85", "q95"):
            if key in latest_point:
                attrs[key] = latest_point[key]

        surface_bundle = snapshot.surface_bundle if isinstance(snapshot.surface_bundle, dict) else None
        if surface_bundle is not None:
            attrs["surface_flow_value"] = surface_bundle.get("flow_value")
            attrs["surface_flow_time"] = _dt_to_iso(surface_bundle.get("flow_time"))
            attrs["surface_level_value"] = surface_bundle.get("level_value")
            attrs["surface_level_time"] = _dt_to_iso(surface_bundle.get("level_time"))
            attrs["surface_water_temperature"] = surface_bundle.get("water_temperature")
            attrs["surface_table_measurement_time"] = _dt_to_iso(
                surface_bundle.get("table_measurement_time")
            )
            attrs["surface_status"] = surface_bundle.get("status_text")
            attrs["surface_status_enum"] = surface_bundle.get("status_enum")
            attrs["surface_spa_stage"] = _surface_spa_stage(surface_bundle.get("status_enum"))
            attrs["surface_is_drought"] = _surface_is_drought(surface_bundle.get("status_enum"))
            if isinstance(surface_bundle.get("flow_status_thresholds"), list):
                attrs["surface_flow_status_thresholds"] = surface_bundle.get("flow_status_thresholds")
            if isinstance(surface_bundle.get("level_status_thresholds"), list):
                attrs["surface_level_status_thresholds"] = surface_bundle.get("level_status_thresholds")
            if isinstance(surface_bundle.get("flow_forecast"), dict) and surface_bundle.get("flow_forecast"):
                attrs["surface_flow_forecast"] = surface_bundle.get("flow_forecast")
            if isinstance(surface_bundle.get("level_forecast"), dict) and surface_bundle.get("level_forecast"):
                attrs["surface_level_forecast"] = surface_bundle.get("level_forecast")
            selected_forecast = _surface_selected_forecast(snapshot)
            if selected_forecast:
                attrs["forecast"] = selected_forecast
            flood_limits = surface_bundle.get("flood_limits")
            if isinstance(flood_limits, dict) and flood_limits:
                attrs["flood_limits"] = flood_limits

        air_bundle = _air_quality_bundle(snapshot)
        if air_bundle is not None:
            pollutants_latest = air_bundle.get("pollutants_latest")
            if isinstance(pollutants_latest, dict) and pollutants_latest:
                attrs["air_quality_pollutants_latest"] = pollutants_latest
            attrs["air_quality_pollutants_time"] = _dt_to_iso(air_bundle.get("pollutants_time"))
            attrs["air_quality_primary_key"] = air_bundle.get("primary_key")
            attrs["air_quality_primary_label"] = air_bundle.get("primary_label")
            attrs["air_quality_primary_unit"] = air_bundle.get("primary_unit")

            supp_latest = air_bundle.get("supplementary_latest")
            if isinstance(supp_latest, dict) and supp_latest:
                attrs["air_quality_supplementary_latest"] = supp_latest
            supp_times = air_bundle.get("supplementary_latest_times")
            if isinstance(supp_times, dict) and supp_times:
                attrs["air_quality_supplementary_times"] = {
                    key: _dt_to_iso(value)
                    for key, value in supp_times.items()
                    if _dt_to_iso(value) is not None
                }

        meteo_bundle = _meteorological_bundle(snapshot)
        if meteo_bundle is not None:
            attrs["meteorological_primary_key"] = meteo_bundle.get("primary_key")
            attrs["meteorological_primary_label"] = meteo_bundle.get("primary_label")
            attrs["meteorological_primary_unit"] = meteo_bundle.get("primary_unit")
            attrs["meteorological_has_snow"] = meteo_bundle.get("snow_has_entity")
            latest_values = meteo_bundle.get("latest_values")
            if isinstance(latest_values, dict) and latest_values:
                attrs["meteorological_latest_values"] = latest_values
            latest_times = meteo_bundle.get("latest_times")
            if isinstance(latest_times, dict) and latest_times:
                attrs["meteorological_latest_times"] = {
                    key: _dt_to_iso(value)
                    for key, value in latest_times.items()
                    if _dt_to_iso(value) is not None
                }

        if snapshot.supports_quantiles:
            attrs.update(_quantile_help_attributes(snapshot, include_all_quantile_meanings=True))
            attrs["quantile_band"] = _current_quantile_band(snapshot)
            attrs["quantile_band_description"] = _current_quantile_band_description(snapshot)

        return {key: value for key, value in attrs.items() if value is not None}


class ChmiUndergroundWaterLastMeasurementSensor(ChmiUndergroundWaterBaseEntity):
    """Timestamp of the last available measurement."""

    _attr_translation_key = "last_measurement"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_measurement")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self):
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.latest_value_time or snapshot.date_of_last_measure

    @property
    def icon(self) -> str:
        return "mdi:clock-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_ATTRIBUTION: ATTRIBUTION}


class ChmiSurfaceWaterFlowSensor(ChmiUndergroundWaterBaseEntity):
    """Surface-water flow sensor (m3/s)."""

    _attr_translation_key = "surface_flow"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "surface_flow")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        return _surface_float(self._snapshot, "flow_value", fallback_key="table_row_q")

    @property
    def native_unit_of_measurement(self) -> str:
        return "m3/s"

    @property
    def icon(self) -> str:
        return "mdi:waves-arrow-right"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(bundle.get("flow_time") or bundle.get("table_measurement_time")),
            "graph_status_thresholds": bundle.get("flow_status_thresholds"),
            "table_value_q": bundle.get("table_row_q"),
        }
        forecast = bundle.get("flow_forecast")
        if isinstance(forecast, dict) and forecast:
            attrs["forecast"] = forecast
            attrs["forecast_points"] = len(forecast)
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiSurfaceWaterLevelSensor(ChmiUndergroundWaterBaseEntity):
    """Surface-water level sensor (cm)."""

    _attr_translation_key = "surface_level"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "surface_level")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        return _surface_float(self._snapshot, "level_value", fallback_key="table_row_h")

    @property
    def native_unit_of_measurement(self) -> str:
        return "cm"

    @property
    def icon(self) -> str:
        return "mdi:waves-arrow-up"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(bundle.get("level_time") or bundle.get("table_measurement_time")),
            "graph_status_thresholds": bundle.get("level_status_thresholds"),
            "table_value_h": bundle.get("table_row_h"),
        }
        forecast = bundle.get("level_forecast")
        if isinstance(forecast, dict) and forecast:
            attrs["forecast"] = forecast
            attrs["forecast_points"] = len(forecast)
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiSurfaceWaterTemperatureSensor(ChmiUndergroundWaterBaseEntity):
    """Surface-water temperature sensor."""

    _attr_translation_key = "water_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "water_temperature")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        return _surface_float(self._snapshot, "water_temperature")

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(bundle.get("table_measurement_time")),
        }
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiSurfaceWaterStatusSensor(ChmiUndergroundWaterBaseEntity):
    """Current SPA/drought status for a surface-water station."""

    _attr_translation_key = "surface_status"

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "surface_status")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        status_text = bundle.get("status_text")
        if isinstance(status_text, str) and status_text.strip():
            return status_text.strip()
        status_enum = bundle.get("status_enum")
        if isinstance(status_enum, str) and status_enum.strip():
            return status_enum.strip()
        return None

    @property
    def icon(self) -> str:
        if _surface_is_drought(_surface_status_enum(self._snapshot)):
            return "mdi:weather-sunny-alert"
        return "mdi:alert-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        status_enum = bundle.get("status_enum")
        flood_limits = bundle.get("flood_limits")
        attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "status_enum": status_enum,
            "status_text": bundle.get("status_text"),
            "spa_stage": _surface_spa_stage(status_enum),
            "is_drought": _surface_is_drought(status_enum),
            "measurement_time": _dt_to_iso(bundle.get("table_measurement_time")),
            "current_level_cm": _surface_float(self._snapshot, "level_value", fallback_key="table_row_h"),
            "current_flow_m3s": _surface_float(self._snapshot, "flow_value", fallback_key="table_row_q"),
        }
        if isinstance(flood_limits, dict) and flood_limits:
            attrs["flood_limits"] = flood_limits
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiSurfaceWaterLimitSensor(ChmiUndergroundWaterBaseEntity):
    """Surface-water SPA / drought limit (cm)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        limit_key: str,
    ) -> None:
        super().__init__(coordinator, entry, f"{limit_key}_limit")
        self._limit_key = limit_key
        self._attr_translation_key = f"{limit_key}_limit"

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        flood_limits = bundle.get("flood_limits")
        if not isinstance(flood_limits, dict):
            return None
        raw_value = flood_limits.get(f"{self._limit_key}_cm")
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        return "cm"

    @property
    def icon(self) -> str:
        if self._limit_key == "sucho":
            return "mdi:weather-sunny-alert"
        return "mdi:alert-decagram-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _surface_bundle(self._snapshot)
        if bundle is None:
            return None
        flood_limits = bundle.get("flood_limits")
        if not isinstance(flood_limits, dict):
            flood_limits = {}
        attrs = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "raw_value": flood_limits.get(f"{self._limit_key}_raw"),
            "status_enum": bundle.get("status_enum"),
            "status_text": bundle.get("status_text"),
            "current_level_cm": _surface_float(self._snapshot, "level_value", fallback_key="table_row_h"),
            "measurement_time": _dt_to_iso(bundle.get("table_measurement_time")),
        }
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiAirQualityPollutantSensor(ChmiUndergroundWaterBaseEntity):
    """Air-quality pollutant sensor (e.g. PM10, NO2)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        pollutant_key: str,
    ) -> None:
        super().__init__(coordinator, entry, f"airq_{pollutant_key.lower()}")
        self._pollutant_key = pollutant_key

    @property
    def name(self) -> str | None:
        return _air_quality_pollutant_label(self._snapshot, self._pollutant_key)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        latest = _air_quality_latest_pollutants(self._snapshot)
        if latest is None:
            return None
        value = latest.get(self._pollutant_key)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _air_quality_pollutant_unit(self._snapshot, self._pollutant_key)

    @property
    def icon(self) -> str:
        icon_map = {
            "PM10_1H": "mdi:blur",
            "PM2_5_1H": "mdi:blur",
            "NO2_1H": "mdi:molecule",
            "O3_1H": "mdi:weather-sunny-alert",
            "SO2_1H": "mdi:factory",
            "CO_1H": "mdi:molecule-co",
        }
        return icon_map.get(self._pollutant_key, "mdi:air-filter")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _air_quality_bundle(self._snapshot)
        if bundle is None:
            return None
        meta = _air_quality_pollutant_meta(self._snapshot, self._pollutant_key) or {}
        attrs: dict[str, Any] = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "pollutant_key": self._pollutant_key,
            "measurement_time": _dt_to_iso(bundle.get("pollutants_time")),
            "code": meta.get("Code"),
            "description": _air_quality_plain_text(meta.get("DescriptionAsHtml")),
            "unit_ascii": meta.get("UnitASCII"),
            "unit_unicode": meta.get("UnitUNICODE"),
        }
        series = _air_quality_pollutant_series(self._snapshot, self._pollutant_key)
        if series:
            attrs["history"] = series
            attrs["history_points"] = len(series)
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiAirQualitySupplementarySensor(ChmiUndergroundWaterBaseEntity):
    """Supplementary air-quality station sensor (temp/humidity/radiation)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        value_key: str,
    ) -> None:
        meta = AIR_QUALITY_SUPPLEMENTARY_META[value_key]
        super().__init__(coordinator, entry, str(meta["suffix"]))
        self._value_key = value_key
        self._meta = meta

        if value_key == "temp2m":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif value_key == "humidity":
            self._attr_device_class = SensorDeviceClass.HUMIDITY

    @property
    def name(self) -> str | None:
        return str(self._meta["name"])

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        bundle = _air_quality_bundle(self._snapshot)
        if bundle is None:
            return None
        latest = bundle.get("supplementary_latest")
        if not isinstance(latest, dict):
            return None
        raw = latest.get(self._value_key)
        if isinstance(raw, (int, float)):
            return float(raw)
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return str(self._meta["unit"])

    @property
    def icon(self) -> str:
        return {
            "temp2m": "mdi:thermometer",
            "humidity": "mdi:water-percent",
            "glrd": "mdi:white-balance-sunny",
        }.get(self._value_key, "mdi:chart-line")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _air_quality_bundle(self._snapshot)
        if bundle is None:
            return None
        times = bundle.get("supplementary_latest_times")
        if not isinstance(times, dict):
            times = {}
        attrs: dict[str, Any] = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(times.get(self._value_key)),
        }
        series = _air_quality_supplementary_series(self._snapshot, self._value_key)
        if series:
            attrs["history"] = series
            attrs["history_points"] = len(series)
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiMeteorologicalParameterSensor(ChmiUndergroundWaterBaseEntity):
    """Meteorological station parameter sensor (snow, temp, humidity, wind...)."""

    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        parameter_key: str,
    ) -> None:
        override = METEOROLOGICAL_PARAM_OVERRIDES.get(parameter_key, {})
        suffix = override.get("suffix") or f"meteo_{parameter_key.lower()}"
        super().__init__(coordinator, entry, str(suffix))
        self._parameter_key = parameter_key
        self._override = override

        if parameter_key not in {"D", "Dmax"}:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        device_class = override.get("device_class")
        if isinstance(device_class, SensorDeviceClass):
            self._attr_device_class = device_class

    @property
    def name(self) -> str | None:
        if isinstance(self._override.get("name"), str):
            return str(self._override["name"])
        return _meteorological_parameter_label(self._snapshot, self._parameter_key)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        latest = _meteorological_latest_values(self._snapshot)
        if latest is None:
            return None
        value = latest.get(self._parameter_key)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        override_unit = self._override.get("unit")
        if isinstance(override_unit, str):
            return override_unit
        return _meteorological_parameter_unit(self._snapshot, self._parameter_key)

    @property
    def icon(self) -> str:
        if isinstance(self._override.get("icon"), str):
            return str(self._override["icon"])
        return "mdi:chart-line"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bundle = _meteorological_bundle(self._snapshot)
        if bundle is None:
            return None
        attrs: dict[str, Any] = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "parameter_key": self._parameter_key,
            "measurement_time": _dt_to_iso(_meteorological_latest_time(self._snapshot, self._parameter_key)),
        }
        meta = _meteorological_parameter_meta(self._snapshot, self._parameter_key)
        if meta:
            attrs["code"] = meta.get("Code")
            attrs["description"] = _air_quality_plain_text(meta.get("Name"))
            attrs["unit_html"] = meta.get("UnitHTML")
        series = _meteorological_parameter_series(self._snapshot, self._parameter_key)
        if series:
            attrs["history"] = series
            attrs["history_points"] = len(series)
        return {key: value for key, value in attrs.items() if value is not None}


class ChmiUndergroundWaterQuantileSensor(ChmiUndergroundWaterBaseEntity):
    """Quantile value (Q5..Q95) from the latest graph point."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: ChmiUndergroundWaterCoordinator,
        entry: ConfigEntry,
        quantile_key: str,
    ) -> None:
        super().__init__(coordinator, entry, quantile_key)
        self._quantile_key = quantile_key
        self._attr_translation_key = quantile_key

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        if snapshot is None or snapshot.latest_point is None:
            return None

        value = snapshot.latest_point.get(self._quantile_key)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.measurement_unit

    @property
    def icon(self) -> str:
        return "mdi:chart-line"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(snapshot.latest_value_time),
            "measurement_label": snapshot.measurement_label,
            "quantile_meaning": _quantile_meaning_text(self._quantile_key),
        }


class ChmiUndergroundWaterStatusSensor(ChmiUndergroundWaterBaseEntity):
    """Text status for groundwater level based on CHMI quantile criteria."""

    _attr_translation_key = "groundwater_status"

    def __init__(self, coordinator: ChmiUndergroundWaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "groundwater_status")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        return _current_quantile_band_chmi_status(self._snapshot)

    @property
    def icon(self) -> str:
        band = _current_quantile_band(self._snapshot)
        if band in {"below_q5", "between_q5_q15"}:
            return "mdi:water-alert"
        if band in {"between_q15_q25", "between_q25_q50", "between_q50_q75"}:
            return "mdi:water-check"
        if band in {"between_q75_q85", "between_q85_q95"}:
            return "mdi:water-plus"
        if band == "above_q95":
            return "mdi:waves-arrow-up"
        return "mdi:water"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        attrs: dict[str, Any] = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "measurement_time": _dt_to_iso(snapshot.latest_value_time),
            "measurement_label": snapshot.measurement_label,
            "quantile_band": _current_quantile_band(snapshot),
            "quantile_band_description": _current_quantile_band_description(snapshot),
            "chmi_status_short": _current_quantile_band_chmi_status_short(snapshot),
        }
        if isinstance(snapshot.latest_point, dict):
            attrs["q5"] = snapshot.latest_point.get("q5")
            attrs["q15"] = snapshot.latest_point.get("q15")
            attrs["q25"] = snapshot.latest_point.get("q25")
            attrs["q50"] = snapshot.latest_point.get("q50")
            attrs["q75"] = snapshot.latest_point.get("q75")
            attrs["q85"] = snapshot.latest_point.get("q85")
            attrs["q95"] = snapshot.latest_point.get("q95")
        return {key: value for key, value in attrs.items() if value is not None}


def _dt_to_iso(value):
    """Serialize datetime to ISO string."""
    if value is None:
        return None
    return value.isoformat()


def _build_surface_water_entities(
    coordinator: ChmiUndergroundWaterCoordinator,
    entry: ConfigEntry,
) -> list[SensorEntity]:
    """Create extra sensors for surface-water profiles."""
    entities: list[SensorEntity] = []

    # Primary `measurement` entity already represents the selected profile.
    if coordinator.measurement_profile != PROFILE_SURFACE_WATER_FLOW:
        entities.append(ChmiSurfaceWaterFlowSensor(coordinator, entry))
    if coordinator.measurement_profile != PROFILE_SURFACE_WATER_LEVEL:
        entities.append(ChmiSurfaceWaterLevelSensor(coordinator, entry))

    entities.extend(
        [
            ChmiSurfaceWaterTemperatureSensor(coordinator, entry),
            ChmiSurfaceWaterStatusSensor(coordinator, entry),
        ]
    )
    entities.extend(
        ChmiSurfaceWaterLimitSensor(coordinator, entry, limit_key) for limit_key in SURFACE_LIMIT_KEYS
    )
    return entities


def _build_air_quality_entities(
    coordinator: ChmiUndergroundWaterCoordinator,
    entry: ConfigEntry,
) -> list[SensorEntity]:
    """Create pollutant + supplementary sensors for air-quality stations."""
    entities: list[SensorEntity] = []
    snapshot = coordinator.data
    bundle = _air_quality_bundle(snapshot)
    if bundle is None:
        return entities

    pollutant_meta = bundle.get("pollutant_meta")
    pollutant_keys: list[str] = []
    if isinstance(pollutant_meta, dict):
        pollutant_keys = [key for key in pollutant_meta if isinstance(key, str)]

    primary_key = bundle.get("primary_key")
    if isinstance(primary_key, str) and primary_key in pollutant_keys:
        pollutant_keys = [key for key in pollutant_keys if key != primary_key]

    ordered_keys = [
        key
        for key in AIR_QUALITY_POLLUTANT_ORDER
        if key in pollutant_keys
    ] + sorted(key for key in pollutant_keys if key not in AIR_QUALITY_POLLUTANT_ORDER)

    entities.extend(
        ChmiAirQualityPollutantSensor(coordinator, entry, pollutant_key)
        for pollutant_key in ordered_keys
    )

    supp_latest = bundle.get("supplementary_latest")
    if isinstance(supp_latest, dict):
        for key in ("temp2m", "humidity", "glrd"):
            if key in supp_latest or _air_quality_supplementary_series(snapshot, key):
                entities.append(ChmiAirQualitySupplementarySensor(coordinator, entry, key))

    return entities


def _build_meteorological_entities(
    coordinator: ChmiUndergroundWaterCoordinator,
    entry: ConfigEntry,
) -> list[SensorEntity]:
    """Create meteorological sensors (snow is primary when available)."""
    entities: list[SensorEntity] = []
    snapshot = coordinator.data
    bundle = _meteorological_bundle(snapshot)
    if bundle is None:
        return entities

    meta = bundle.get("parameter_meta")
    latest = bundle.get("latest_values")
    series = bundle.get("parameter_series")
    keys: set[str] = set()
    if isinstance(meta, dict):
        keys.update(key for key in meta if isinstance(key, str))
    if isinstance(latest, dict):
        keys.update(key for key in latest if isinstance(key, str))
    if isinstance(series, dict):
        keys.update(key for key, value in series.items() if isinstance(key, str) and isinstance(value, dict) and value)

    primary_key = bundle.get("primary_key")
    if isinstance(primary_key, str):
        keys.discard(primary_key)

    ordered = [key for key in METEOROLOGICAL_PARAMETER_ORDER if key in keys]
    ordered.extend(sorted(key for key in keys if key not in METEOROLOGICAL_PARAMETER_ORDER))

    entities.extend(
        ChmiMeteorologicalParameterSensor(coordinator, entry, parameter_key)
        for parameter_key in ordered
    )
    return entities


def _surface_bundle(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, Any] | None:
    """Return normalized surface bundle if available."""
    if snapshot is None:
        return None
    bundle = snapshot.surface_bundle
    if isinstance(bundle, dict):
        return bundle
    return None


def _air_quality_location(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return air-quality station location from metadata."""
    if snapshot is None or not isinstance(snapshot.station_metadata, dict):
        return None
    add_info = snapshot.station_metadata.get("addInfo")
    if not isinstance(add_info, dict):
        return None
    value = add_info.get("location")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _air_quality_district(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return air-quality station district from metadata."""
    if snapshot is None or not isinstance(snapshot.station_metadata, dict):
        return None
    add_info = snapshot.station_metadata.get("addInfo")
    if not isinstance(add_info, dict):
        return None
    value = add_info.get("districtName")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _meteorological_bundle(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, Any] | None:
    """Return normalized meteorological bundle if available."""
    if snapshot is None:
        return None
    bundle = getattr(snapshot, "meteorological_bundle", None)
    if isinstance(bundle, dict):
        return bundle
    return None


def _meteorological_latest_values(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, float] | None:
    """Return latest meteorological values by parameter code."""
    bundle = _meteorological_bundle(snapshot)
    if bundle is None:
        return None
    latest = bundle.get("latest_values")
    if isinstance(latest, dict):
        return latest
    return None


def _meteorological_latest_time(
    snapshot: ChmiGroundwaterSnapshot | None,
    parameter_key: str,
):
    """Return latest timestamp for a meteorological parameter."""
    bundle = _meteorological_bundle(snapshot)
    if bundle is None:
        return None
    latest_times = bundle.get("latest_times")
    if not isinstance(latest_times, dict):
        return None
    return latest_times.get(parameter_key)


def _meteorological_parameter_meta(
    snapshot: ChmiGroundwaterSnapshot | None,
    parameter_key: str,
) -> dict[str, Any] | None:
    """Return meteorological metadata (dial) for a parameter code."""
    bundle = _meteorological_bundle(snapshot)
    if bundle is None:
        return None
    meta = bundle.get("parameter_meta")
    if not isinstance(meta, dict):
        return None
    row = meta.get(parameter_key)
    if isinstance(row, dict):
        return row
    return None


def _meteorological_parameter_series(
    snapshot: ChmiGroundwaterSnapshot | None,
    parameter_key: str,
) -> dict[str, float] | None:
    """Return meteorological history series for a parameter."""
    bundle = _meteorological_bundle(snapshot)
    if bundle is None:
        return None
    series = bundle.get("parameter_series")
    if not isinstance(series, dict):
        return None
    row = series.get(parameter_key)
    if isinstance(row, dict) and row:
        return row
    return None


def _meteorological_parameter_label(
    snapshot: ChmiGroundwaterSnapshot | None,
    parameter_key: str,
) -> str:
    """Human-friendly label for meteorological parameter."""
    meta = _meteorological_parameter_meta(snapshot, parameter_key) or {}
    name = meta.get("Name")
    if isinstance(name, str) and name.strip():
        return _air_quality_plain_text(name) or parameter_key
    override = METEOROLOGICAL_PARAM_OVERRIDES.get(parameter_key, {})
    if isinstance(override.get("name"), str):
        return str(override["name"])
    return parameter_key


def _meteorological_parameter_unit(
    snapshot: ChmiGroundwaterSnapshot | None,
    parameter_key: str,
) -> str | None:
    """Normalize unit from meteorological `dial.UnitHTML`."""
    meta = _meteorological_parameter_meta(snapshot, parameter_key) or {}
    unit = meta.get("UnitHTML")
    if not isinstance(unit, str) or not unit.strip():
        return None
    text = unit
    text = text.replace("m.s<sup>-1</sup>", "m/s")
    text = re.sub(r"<sup>(.*?)</sup>", r"^\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace(" ", "").strip()
    return text or None


def _air_quality_bundle(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, Any] | None:
    """Return normalized air-quality bundle if available."""
    if snapshot is None:
        return None
    bundle = getattr(snapshot, "air_quality_bundle", None)
    if isinstance(bundle, dict):
        return bundle
    return None


def _air_quality_latest_pollutants(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, float] | None:
    """Return latest air-quality pollutant values."""
    bundle = _air_quality_bundle(snapshot)
    if bundle is None:
        return None
    latest = bundle.get("pollutants_latest")
    if isinstance(latest, dict):
        return latest
    return None


def _air_quality_pollutant_meta(
    snapshot: ChmiGroundwaterSnapshot | None,
    pollutant_key: str,
) -> dict[str, Any] | None:
    """Return metadata from CHMI `dial` for a pollutant."""
    bundle = _air_quality_bundle(snapshot)
    if bundle is None:
        return None
    meta_all = bundle.get("pollutant_meta")
    if not isinstance(meta_all, dict):
        return None
    meta = meta_all.get(pollutant_key)
    if isinstance(meta, dict):
        return meta
    return None


def _air_quality_pollutant_label(
    snapshot: ChmiGroundwaterSnapshot | None,
    pollutant_key: str,
) -> str:
    """Build human-friendly pollutant sensor name."""
    meta = _air_quality_pollutant_meta(snapshot, pollutant_key) or {}
    code = meta.get("Code") if isinstance(meta.get("Code"), str) else pollutant_key.split("_", 1)[0]
    if isinstance(code, str):
        code = code.replace("PM2_5", "PM2.5")
    suffix = ""
    if pollutant_key.endswith("_1H"):
        suffix = " (1h)"
    elif pollutant_key.endswith("_24H"):
        suffix = " (24h)"
    return f"{code}{suffix}"


def _air_quality_pollutant_unit(
    snapshot: ChmiGroundwaterSnapshot | None,
    pollutant_key: str,
) -> str | None:
    """Return pollutant unit."""
    meta = _air_quality_pollutant_meta(snapshot, pollutant_key) or {}
    for key in ("UnitASCII", "UnitUNICODE"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _air_quality_pollutant_series(
    snapshot: ChmiGroundwaterSnapshot | None,
    pollutant_key: str,
) -> dict[str, float] | None:
    """Return pollutant history series {iso_time: value}."""
    bundle = _air_quality_bundle(snapshot)
    if bundle is None:
        return None
    series_all = bundle.get("pollutant_series")
    if not isinstance(series_all, dict):
        return None
    series = series_all.get(pollutant_key)
    if isinstance(series, dict) and series:
        return series
    return None


def _air_quality_supplementary_series(
    snapshot: ChmiGroundwaterSnapshot | None,
    key: str,
) -> dict[str, float] | None:
    """Return supplementary air-quality history series {iso_time: value}."""
    bundle = _air_quality_bundle(snapshot)
    if bundle is None:
        return None
    series_all = bundle.get("supplementary_series")
    if not isinstance(series_all, dict):
        return None
    series = series_all.get(key)
    if isinstance(series, dict) and series:
        return series
    return None


def _air_quality_plain_text(value: Any) -> str | None:
    """Strip simple HTML tags from CHMI labels/descriptions."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"<[^>]+>", "", value).strip()
    return text or None


def _surface_float(
    snapshot: ChmiGroundwaterSnapshot | None,
    key: str,
    *,
    fallback_key: str | None = None,
) -> float | None:
    """Read float value from `surface_bundle`, optionally with fallback key."""
    bundle = _surface_bundle(snapshot)
    if bundle is None:
        return None
    for candidate in (key, fallback_key):
        if not candidate:
            continue
        raw = bundle.get(candidate)
        if isinstance(raw, (int, float)):
            return float(raw)
    return None


def _surface_status_enum(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return normalized surface status enum from bundle."""
    bundle = _surface_bundle(snapshot)
    if bundle is None:
        return None
    value = bundle.get("status_enum")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _surface_selected_forecast(snapshot: ChmiGroundwaterSnapshot | None) -> dict[str, float] | None:
    """Return forecast series matching the selected primary surface profile."""
    bundle = _surface_bundle(snapshot)
    if bundle is None or snapshot is None:
        return None
    key = (
        "flow_forecast"
        if getattr(snapshot, "measurement_profile", None) == PROFILE_SURFACE_WATER_FLOW
        else "level_forecast"
    )
    forecast = bundle.get(key)
    if isinstance(forecast, dict) and forecast:
        return forecast
    return None


def _surface_spa_stage(status_enum: Any) -> int | None:
    """Parse SPA stage number (1-4) from CHMI enum."""
    if not isinstance(status_enum, str):
        return None
    if "SUCHO" in status_enum.upper():
        return None
    for stage in (1, 2, 3, 4):
        if f"SPA_{stage}" in status_enum.upper():
            return stage
    return 0 if "NSTAV" in status_enum.upper() else None


def _surface_is_drought(status_enum: Any) -> bool | None:
    """Return whether current status represents drought."""
    if not isinstance(status_enum, str):
        return None
    upper = status_enum.upper()
    if "SUCHO" in upper:
        return True
    if "SPA_" in upper or "NSTAV" in upper:
        return False
    return None


def _build_device_info_compat(kwargs: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo with fallback for older HA versions."""
    try:
        return DeviceInfo(**kwargs)
    except TypeError:
        fallback = dict(kwargs)
        for key in ("suggested_area", "hw_version", "serial_number"):
            fallback.pop(key, None)
        return DeviceInfo(**fallback)


def _device_name(station_name: str | None, station_code: str | None, station_id: str) -> str:
    """Build a more descriptive device name."""
    base = station_name or f"CHMI {station_id}"
    if station_code and station_code not in base:
        return f"{base} ({station_code})"
    return base


def _device_identifier(
    coordinator: ChmiUndergroundWaterCoordinator,
    snapshot: ChmiGroundwaterSnapshot | None,
) -> str:
    """Return stable device identifier namespace+station id."""
    station_family = getattr(snapshot, "station_family", None)
    if not isinstance(station_family, str):
        if coordinator.measurement_profile in (
            PROFILE_SURFACE_WATER_FLOW,
            PROFILE_SURFACE_WATER_LEVEL,
        ):
            station_family = "surface_water"
        elif coordinator.measurement_profile == PROFILE_AIR_QUALITY:
            station_family = "air_quality"
        elif coordinator.measurement_profile == PROFILE_METEOROLOGICAL:
            station_family = "meteorological"
        else:
            station_family = "groundwater"
    return f"{station_family}:{coordinator.station_id}"


def _device_model(snapshot: ChmiGroundwaterSnapshot | None, obj_type: str | None) -> str | None:
    """Build readable device model string."""
    if obj_type is None:
        profile = getattr(snapshot, "measurement_profile", None)
        if profile == PROFILE_SURFACE_WATER_FLOW:
            return "Povrchove vody - prutok"
        if profile == PROFILE_SURFACE_WATER_LEVEL:
            return "Povrchove vody - vodni stav"
        if profile == PROFILE_AIR_QUALITY:
            return "Kvalita ovzdusi"
        if profile == PROFILE_METEOROLOGICAL:
            return "Meteorologicka stanice"
        if profile == PROFILE_GROUNDWATER:
            return "Podzemni vody"
        return None
    if obj_type in OBJ_TYPE_LABELS_CS:
        return f"{OBJ_TYPE_LABELS_CS[obj_type]} (CHMI podzemni vody)"
    return f"{obj_type.replace('_', ' ').title()} (CHMI podzemni vody)"


def _quantile_meaning_text(quantile_key: str) -> str:
    """Return a human-readable explanation for a quantile."""
    meanings = {
        "q5": "Q5: velmi nizke referencni pasmo (priblizne 5. percentil).",
        "q15": "Q15: nizke referencni pasmo.",
        "q25": "Q25: dolni kvartil (25. percentil).",
        "q50": "Q50: median (typicka / stredni hodnota).",
        "q75": "Q75: horni kvartil (75. percentil).",
        "q85": "Q85: vyssi referencni pasmo.",
        "q95": "Q95: velmi vysoke referencni pasmo (priblizne 95. percentil).",
    }
    return meanings.get(quantile_key, quantile_key.upper())


def _quantile_help_attributes(
    snapshot: ChmiGroundwaterSnapshot,
    *,
    include_all_quantile_meanings: bool,
) -> dict[str, Any]:
    """Build shared quantile-help attributes."""
    attrs: dict[str, Any] = {
        "quantiles_help": (
            "Q5-Q95 jsou referencni kvantily CHMI pro porovnani aktualni hodnoty "
            "s dlouhodobym rozdelenim dat (pro dane obdobi). Q50 je median."
        )
    }
    if include_all_quantile_meanings:
        for key in QUANTILE_KEYS:
            attrs[f"{key}_meaning"] = _quantile_meaning_text(key)
    return attrs


def _current_quantile_band(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return a machine-friendly band identifier for the latest value vs. quantiles."""
    if snapshot is None or snapshot.latest_point is None or snapshot.latest_value is None:
        return None

    point = snapshot.latest_point
    value = snapshot.latest_value
    thresholds: list[tuple[str, float]] = []
    for key in QUANTILE_KEYS:
        raw = point.get(key)
        if isinstance(raw, (int, float)):
            thresholds.append((key, float(raw)))

    if len(thresholds) < 2:
        return None

    if value < thresholds[0][1]:
        return f"below_{thresholds[0][0]}"

    for idx in range(len(thresholds) - 1):
        lower_key, lower_val = thresholds[idx]
        upper_key, upper_val = thresholds[idx + 1]
        if lower_val <= value < upper_val:
            return f"between_{lower_key}_{upper_key}"

    return f"above_{thresholds[-1][0]}"


def _current_quantile_band_description(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return a human-readable interpretation of the quantile band."""
    band = _current_quantile_band(snapshot)
    if band is None:
        return None

    descriptions = {
        "below_q5": "Aktualni hodnota je pod Q5 (velmi nizke pasmo).",
        "between_q5_q15": "Aktualni hodnota je mezi Q5 a Q15 (nizke pasmo).",
        "between_q15_q25": "Aktualni hodnota je mezi Q15 a Q25 (spise nizka).",
        "between_q25_q50": "Aktualni hodnota je mezi Q25 a Q50 (mirne pod medianem).",
        "between_q50_q75": "Aktualni hodnota je mezi Q50 a Q75 (mirne nad medianem).",
        "between_q75_q85": "Aktualni hodnota je mezi Q75 a Q85 (spise vysoka).",
        "between_q85_q95": "Aktualni hodnota je mezi Q85 a Q95 (vysoke pasmo).",
        "above_q95": "Aktualni hodnota je nad Q95 (velmi vysoke pasmo).",
    }
    return descriptions.get(band, band)


def _current_quantile_band_chmi_status(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return CHMI-style groundwater status text derived from quantile band."""
    band = _current_quantile_band(snapshot)
    if band is None:
        return None

    statuses = {
        "below_q5": "0 az Q5 mimoradne podnormalni stav",
        "between_q5_q15": "Q5 az Q15 silne podnormalni stav",
        "between_q15_q25": "Q15 az Q25 mirne podnormalni stav",
        "between_q25_q50": "Q25 az Q75 normalni stav",
        "between_q50_q75": "Q25 az Q75 normalni stav",
        "between_q75_q85": "Q75 az Q85 mirne nadnormalni stav",
        "between_q85_q95": "Q85 az Q95 silne nadnormalni stav",
        "above_q95": "vetsi nez Q95 mimoradne nadnormalni stav",
    }
    return statuses.get(band, band)


def _current_quantile_band_chmi_status_short(snapshot: ChmiGroundwaterSnapshot | None) -> str | None:
    """Return short CHMI-style status label without quantile range."""
    band = _current_quantile_band(snapshot)
    if band is None:
        return None
    short_statuses = {
        "below_q5": "mimoradne podnormalni",
        "between_q5_q15": "silne podnormalni",
        "between_q15_q25": "mirne podnormalni",
        "between_q25_q50": "normalni",
        "between_q50_q75": "normalni",
        "between_q75_q85": "mirne nadnormalni",
        "between_q85_q95": "silne nadnormalni",
        "above_q95": "mimoradne nadnormalni",
    }
    return short_statuses.get(band, band)
