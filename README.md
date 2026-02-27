[![](https://img.shields.io/github/release/VitisEK/chmi_measured_data/all.svg?style=for-the-badge)](https://github.com/VitisEK/chmi_measured_data/releases)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![](https://img.shields.io/github/license/VitisEK/chmi_measured_data?style=for-the-badge)](https://github.com/VitisEK/chmi_measured_data)
[![](https://img.shields.io/badge/MAINTAINER-%40VitisEK-red?style=for-the-badge)](https://github.com/VitisEK)
[![](https://img.shields.io/badge/GitHub%20Sponsors-SUPPORT-EA4AAA?style=for-the-badge&logo=githubsponsors&logoColor=white)](https://github.com/sponsors/VitisEK)
[![](https://img.shields.io/badge/COMMUNITY-FORUM-success?style=for-the-badge)](https://community.home-assistant.io)

# CHMI Measured Data (`chmi_measured_data`)

Custom Home Assistant integration for CHMI measured stations (groundwater, surface water, air quality, meteorological).

## Features

- UI configuration using CHMI station URL
- Support for 4 station types:
  - Groundwater
  - Surface water
  - Air quality
  - Meteorological
- Flexible update schedule:
  - Daily at a fixed local time
  - Interval polling (minutes)
- Automatic sensor set by selected station type
- Rich sensor attributes (metadata, timestamps, station details, forecast/history where available)

## Installation (local)

1. Copy `custom_components/chmi_measured_data` into your Home Assistant config directory under `custom_components/`.
2. Restart Home Assistant.
3. Add integration: Settings -> Devices & Services -> Add Integration -> `CHMI Measured Data`.

## Configuration

### Inputs (GUI)

- `station_url`: CHMI station page URL
- `name`: custom integration name (optional)
- `station_family`: station type (must match URL type)
- `update_mode`: `daily_time` or `interval`

### Options (GUI)

- `station_url`: update station URL later in Options
- `update_mode`: keep fixed-time daily refresh, or switch to interval polling
- `update_time`: time in `HH:MM` when `daily_time` is selected
- `scan_interval_minutes`: interval in minutes when `interval` is selected

## Outputs

### Common

- `measurement` sensor (primary value from selected profile)
- `last_measurement` sensor
- Device metadata and CHMI attribution

### Groundwater profile

- `groundwater_status` (CHMI criteria)
- Quantile sensors: `q5`, `q15`, `q25`, `q50`, `q75`, `q85`, `q95`

### Surface-water profiles

- Flow and water-level sensors (primary + complementary)
- Water temperature sensor
- SPA / drought status sensor
- Limit sensors: `spa_1`, `spa_2`, `spa_3`, `spa_4`, `sucho`
- Forecast attributes (when CHMI provides forecast points)

### Air-quality profile

- Main pollutant sensor (primary pollutant selected from CHMI data)
- Additional pollutant sensors (for available pollutant series)
- Supplementary sensors: air temperature, humidity, global radiation (when available)

### Meteorological profile

- Primary meteorological measurement sensor
- Additional parameter sensors (for available CHMI parameter series), e.g. snow, temperature, humidity, wind, precipitation

## Notes

- URL type and selected station type must match.
- Data source: CHMI (`data-provider.chmi.cz` and `chmi.cz` station pages).
- Each entry is uniquely identified by station id + selected profile.
- Refresh is handled by integration scheduler (daily fixed time or interval).

## Changelog

### v0.3.0

- Current integration version (`manifest.json`)

## Support

- Open an issue in this repository for bugs or feature requests.
- Community discussions: [Home Assistant Community Forum](https://community.home-assistant.io)
