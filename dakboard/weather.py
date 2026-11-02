"""Today's weather from Open-Meteo - no API key, attribution required.

One call returns the current conditions and a 7-day outlook.
The gazetteer behind /v1/search has TOWNS ONLY, so "Gosden" is not in it
but "Welwyn Garden City" is.
"""

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ATTRIBUTION = "Weather by Open-Meteo.com"

USER_AGENT = "dakboard/1.0 (self-hosted family board)"

# WMO weather interpretation codes -> (label, icon key)
WMO = {
    0:  ("Clear sky",         "clear"),
    1:  ("Mainly clear",      "clear"),
    2:  ("Partly cloudy",     "partly"),
    3:  ("Overcast",          "cloud"),
    45: ("Fog",               "fog"),
    48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle",     "drizzle"),
    53: ("Drizzle",           "drizzle"),
    55: ("Heavy drizzle",     "drizzle"),
    56: ("Light freezing drizzle", "sleet"),
    57: ("Heavy freezing drizzle", "sleet"),
    61: ("Light rain",        "rain"),
    63: ("Moderate rain",     "rain"),
    65: ("Heavy rain",        "rain"),
    66: ("Light freezing rain", "sleet"),
    67: ("Heavy freezing rain", "sleet"),
    71: ("Light snow",        "snow"),
    73: ("Snow",              "snow"),
    75: ("Heavy snow",        "snow"),
    77: ("Snow grains",       "snow"),
    80: ("Light showers",     "showers"),
    81: ("Showers",           "showers"),
    82: ("Violent showers",   "showers"),
    85: ("Light snow showers", "snow"),
    86: ("Snow showers",      "snow"),
    95: ("Thunderstorm",      "storm"),
    96: ("Thunderstorm with hail", "storm"),
    99: ("Thunderstorm with hail", "storm"),
}


class WeatherError(Exception):
    """Raised when the weather cannot be read."""


def geocode(place):
    """Look up a place. Returns (lat, lon, name) or raises."""
    params = {"name": place, "count": "1", "language": "en", "format": "json"}
    url = f"{GEOCODE_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise WeatherError(f"Geocoding failed (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise WeatherError(f"Could not reach Open-Meteo: {exc.reason}") from exc
    results = data.get("results") or []
    if not results:
        raise WeatherError(
            f"Could not find '{place}'. The gazetteer only knows towns - try a "
            "nearby town name."
        )
    first = results[0]
    return (str(first["latitude"]), str(first["longitude"]), first.get("name", place))


def fetch(latitude, longitude, tz="Europe/London", days=7):
    """Current conditions + a daily outlook. Returns a dict."""
    if not latitude or not longitude:
        raise WeatherError("No location set for the weather.")

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": tz,
        "forecast_days": str(days),
    }
    url = f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise WeatherError(f"Open-Meteo error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WeatherError(f"Could not reach Open-Meteo: {exc.reason}") from exc

    current = data.get("current") or {}
    code = current.get("weather_code")
    label, icon = WMO.get(code, ("Unknown", "cloud"))

    days_out = []
    daily = data.get("daily") or {}
    for i, date in enumerate(daily.get("time", []) or []):
        days_out.append({
            "date": date,
            "code": (daily.get("weather_code") or [None])[i]
                    if i < len(daily.get("weather_code") or []) else None,
            "max": (daily.get("temperature_2m_max") or [None])[i]
                   if i < len(daily.get("temperature_2m_max") or []) else None,
            "min": (daily.get("temperature_2m_min") or [None])[i]
                   if i < len(daily.get("temperature_2m_min") or []) else None,
            "precip": (daily.get("precipitation_sum") or [None])[i]
                      if i < len(daily.get("precipitation_sum") or []) else None,
        })

    for entry in days_out:
        if entry["code"] is not None:
            entry["label"], entry["icon"] = WMO.get(entry["code"], ("Unknown", "cloud"))

    return {
        "temp": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "precipitation": current.get("precipitation"),
        "code": code,
        "label": label,
        "icon": icon,
        "days": days_out,
        "attribution": ATTRIBUTION,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def celsius_to_fahrenheit(value):
    if value is None:
        return None
    return value * 9 / 5 + 32


def convert(value, units):
    if value is None:
        return None
    return celsius_to_fahrenheit(value) if units == "fahrenheit" else value
