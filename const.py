"""Constants for the CHMI measured data integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "chmi_measured_data"

CONF_STATION_URL = "station_url"
CONF_STATION_ID = "station_id"
CONF_MEASUREMENT_PROFILE = "measurement_profile"
CONF_SCAN_INTERVAL = "scan_interval_minutes"
CONF_UPDATE_MODE = "update_mode"
CONF_UPDATE_TIME = "update_time"

DEFAULT_SCAN_INTERVAL_MINUTES = 60
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 1440

PROFILE_GROUNDWATER = "groundwater"
PROFILE_SURFACE_WATER_FLOW = "surface_water_flow"
PROFILE_SURFACE_WATER_LEVEL = "surface_water_level"
PROFILE_AIR_QUALITY = "air_quality"
PROFILE_METEOROLOGICAL = "meteorological"
DEFAULT_MEASUREMENT_PROFILE = PROFILE_GROUNDWATER
MEASUREMENT_PROFILES = (
    PROFILE_GROUNDWATER,
    PROFILE_SURFACE_WATER_FLOW,
    PROFILE_SURFACE_WATER_LEVEL,
    PROFILE_AIR_QUALITY,
    PROFILE_METEOROLOGICAL,
)

UPDATE_MODE_INTERVAL = "interval"
UPDATE_MODE_DAILY_TIME = "daily_time"
DEFAULT_UPDATE_MODE = UPDATE_MODE_DAILY_TIME
DEFAULT_UPDATE_TIME = "07:00"

DATA_COORDINATOR = "coordinator"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CHMI_STATION_HOST_SUFFIX = "chmi.cz"
CHMI_DATA_PROVIDER_BASE = "https://data-provider.chmi.cz"
CHMI_STATION_DATA_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/stations/data/pz/{{station_id}}"
CHMI_GROUNDWATER_GRAPH_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.groundwater/{{station_id}}"
CHMI_SURFACE_WATER_STATION_DATA_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/stations/data/prf/{{station_id}}"
CHMI_SURFACE_WATER_FLOW_GRAPH_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.water-flow/{{station_id}}"
CHMI_SURFACE_WATER_LEVEL_GRAPH_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.water-level/{{station_id}}"
CHMI_SURFACE_WATER_MEASURED_TABLE_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/data/tab/stanice.namerena-data/{{station_id}}"
CHMI_SURFACE_WATER_FLOOD_LIMITS_TABLE_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/data/tab/limity.povoden/{{station_id}}"
CHMI_AIR_QUALITY_STATION_DATA_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/stations/data/ovzdusi/{{station_id}}"
CHMI_AIR_QUALITY_GRAPH_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.ovzdusi-kvalita/{{station_id}}"
CHMI_AIR_QUALITY_SUPPLEMENTARY_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.ovzdusi-doplnkova-data/{{station_id}}"
)
CHMI_METEOROLOGICAL_STATION_DATA_URL = f"{CHMI_DATA_PROVIDER_BASE}/api/stations/data/meteo/{{station_id}}"
CHMI_METEOROLOGICAL_KLIMA_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.meteo-stanice.klima-10m/{{station_id}}"
)
CHMI_METEOROLOGICAL_TEMPERATURE_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.meteo-stanice.teplota-10m/{{station_id}}"
)
CHMI_METEOROLOGICAL_WIND_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.meteo-stanice.vitr-10m/{{station_id}}"
)
CHMI_METEOROLOGICAL_PRECIP_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.meteo-stanice.srazky-10m/{{station_id}}"
)
CHMI_METEOROLOGICAL_SNOW_DAILY_GRAPH_URL = (
    f"{CHMI_DATA_PROVIDER_BASE}/api/graphs/graf.meteo-stanice.snih-dly/{{station_id}}"
)
