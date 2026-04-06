#!/usr/bin/env python3
"""
Kite Conditions — 7-day kiteboarding forecast for your favorite spots.

Fetches hourly wind, temperature, and daylight data from the Open-Meteo API
and displays a clean, filterable forecast for daylight hours only.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── Configuration ────────────────────────────────────────────────────────────

SPOTS_FILE = Path(__file__).parent / "spots.json"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_PARAMS = [
    "temperature_2m",
    "apparent_temperature",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "weather_code",
    "is_day",
]

DAILY_PARAMS = [
    "sunrise",
    "sunset",
    "daylight_duration",
    "temperature_2m_max",
    "temperature_2m_min",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
]

# Wind thresholds in mph for kiteability rating
WIND_IDEAL_MIN = 15
WIND_IDEAL_MAX = 30
WIND_MARGINAL_MIN = 12
WIND_MARGINAL_MAX = 35

# WMO weather codes → short descriptions
WMO_CODES = {
    0: "Clear",
    1: "Mostly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Heavy Drizzle",
    61: "Light Rain",
    63: "Rain",
    65: "Heavy Rain",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    80: "Light Showers",
    81: "Showers",
    82: "Heavy Showers",
    95: "Thunderstorm",
    96: "T-storm + Hail",
    99: "T-storm + Heavy Hail",
}

# Compass directions
COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def degrees_to_compass(deg: float) -> str:
    """Convert wind direction in degrees to a compass abbreviation."""
    idx = round(deg / 22.5) % 16
    return COMPASS[idx]


def kite_rating(wind_mph: float) -> str:
    """Return a kiteability rating emoji + label."""
    if WIND_IDEAL_MIN <= wind_mph <= WIND_IDEAL_MAX:
        return "🟢 SEND IT"
    elif WIND_MARGINAL_MIN <= wind_mph < WIND_IDEAL_MIN:
        return "🟡 MAYBE"
    elif WIND_IDEAL_MAX < wind_mph <= WIND_MARGINAL_MAX:
        return "🟡 MAYBE"
    else:
        return "🔴 NOPE"


def weather_desc(code: int) -> str:
    """Convert WMO weather code to a short description."""
    return WMO_CODES.get(code, f"Code {code}")


def load_spots() -> list[dict]:
    """Load kiteboard spots from spots.json."""
    if not SPOTS_FILE.exists():
        print(f"❌ Spots file not found: {SPOTS_FILE}")
        print("   Create a spots.json file — see README.md for format.")
        sys.exit(1)

    with open(SPOTS_FILE) as f:
        spots = json.load(f)

    if not spots:
        print("❌ No spots defined in spots.json")
        sys.exit(1)

    return spots


# ── API ──────────────────────────────────────────────────────────────────────

def fetch_forecast(lat: float, lon: float) -> dict:
    """Fetch a 7-day hourly + daily forecast from Open-Meteo."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_PARAMS),
        "daily": ",".join(DAILY_PARAMS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": 7,
    }

    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Display ──────────────────────────────────────────────────────────────────

def print_header():
    """Print the app header."""
    print()
    print("=" * 72)
    print("  🪁  KITE CONDITIONS — 7-Day Forecast")
    print(f"  Generated: {datetime.now().strftime('%A, %B %d %Y at %I:%M %p')}")
    print("=" * 72)


def print_spot_forecast(spot: dict, data: dict):
    """Print the full forecast for a single spot."""
    name = spot["name"]
    tz = data.get("timezone", "UTC")

    print()
    print(f"  📍 {name}")
    print(f"     Coordinates: {spot['lat']:.4f}, {spot['lon']:.4f}  |  Timezone: {tz}")
    print("-" * 72)

    hourly = data["hourly"]
    daily = data["daily"]
    times = hourly["time"]

    # Group hourly data by date
    days: dict[str, list[int]] = {}
    for i, t in enumerate(times):
        date_str = t[:10]
        if date_str not in days:
            days[date_str] = []
        days[date_str].append(i)

    for day_idx, (date_str, hour_indices) in enumerate(days.items()):
        # Daily summary
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = dt.strftime("%A, %b %d")

        sunrise = daily["sunrise"][day_idx][-5:] if day_idx < len(daily["sunrise"]) else "?"
        sunset = daily["sunset"][day_idx][-5:] if day_idx < len(daily["sunset"]) else "?"
        hi = daily["temperature_2m_max"][day_idx] if day_idx < len(daily["temperature_2m_max"]) else "?"
        lo = daily["temperature_2m_min"][day_idx] if day_idx < len(daily["temperature_2m_min"]) else "?"
        max_wind = daily["wind_speed_10m_max"][day_idx] if day_idx < len(daily["wind_speed_10m_max"]) else 0
        max_gust = daily["wind_gusts_10m_max"][day_idx] if day_idx < len(daily["wind_gusts_10m_max"]) else 0
        dom_dir_deg = daily["wind_direction_10m_dominant"][day_idx] if day_idx < len(daily["wind_direction_10m_dominant"]) else 0
        dom_dir = degrees_to_compass(dom_dir_deg) if dom_dir_deg is not None else "?"

        rating = kite_rating(max_wind) if isinstance(max_wind, (int, float)) else "?"

        print()
        print(f"  ┌─ {day_name} {'─' * (52 - len(day_name))}")
        print(f"  │  ☀️  {sunrise} → {sunset}   🌡️  {lo}°F – {hi}°F")
        print(f"  │  🌬️  Max wind: {max_wind} mph ({dom_dir})  Gusts: {max_gust} mph")
        print(f"  │  Rating: {rating}")
        print(f"  │")

        # Hourly breakdown — daylight hours only
        print(f"  │  {'Hour':<7} {'Temp':>5} {'Feels':>6} {'Wind':>6} {'Gust':>6} {'Dir':>5} {'Sky':<16} {'Rating'}")
        print(f"  │  {'─'*7} {'─'*5} {'─'*6} {'─'*6} {'─'*6} {'─'*5} {'─'*16} {'─'*12}")

        daylight_hours = 0
        for i in hour_indices:
            is_day = hourly["is_day"][i]
            if not is_day:
                continue

            daylight_hours += 1
            hour = times[i][-5:]
            temp = hourly["temperature_2m"][i]
            feels = hourly["apparent_temperature"][i]
            wind = hourly["wind_speed_10m"][i]
            gust = hourly["wind_gusts_10m"][i]
            direction = degrees_to_compass(hourly["wind_direction_10m"][i]) if hourly["wind_direction_10m"][i] is not None else "?"
            sky = weather_desc(hourly["weather_code"][i]) if hourly["weather_code"][i] is not None else "?"
            hr_rating = kite_rating(wind) if isinstance(wind, (int, float)) else "?"

            temp_str = f"{temp:.0f}°F" if isinstance(temp, (int, float)) else "?"
            feels_str = f"{feels:.0f}°F" if isinstance(feels, (int, float)) else "?"
            wind_str = f"{wind:.0f}" if isinstance(wind, (int, float)) else "?"
            gust_str = f"{gust:.0f}" if isinstance(gust, (int, float)) else "?"

            print(f"  │  {hour:<7} {temp_str:>5} {feels_str:>6} {wind_str:>5}  {gust_str:>5}  {direction:>4}  {sky:<16} {hr_rating}")

        if daylight_hours == 0:
            print(f"  │  (no daylight data)")

        print(f"  └{'─' * 70}")


def print_best_days_summary(results: list[tuple[dict, dict]]):
    """Print a quick summary of the best days across all spots."""
    print()
    print()
    print("=" * 72)
    print("  🏆  BEST DAYS TO KITE (next 7 days)")
    print("=" * 72)
    print()
    print(f"  {'Spot':<30} {'Day':<18} {'Max Wind':>9} {'Dir':>5} {'Rating'}")
    print(f"  {'─'*30} {'─'*18} {'─'*9} {'─'*5} {'─'*12}")

    for spot, data in results:
        daily = data["daily"]
        for i in range(len(daily["time"])):
            max_wind = daily["wind_speed_10m_max"][i]
            if max_wind is None:
                continue
            rating = kite_rating(max_wind)
            if "SEND IT" not in rating and "MAYBE" not in rating:
                continue

            dt = datetime.strptime(daily["time"][i], "%Y-%m-%d")
            day_str = dt.strftime("%a %b %d")
            dom_dir = degrees_to_compass(daily["wind_direction_10m_dominant"][i]) if daily["wind_direction_10m_dominant"][i] is not None else "?"

            print(f"  {spot['name']:<30} {day_str:<18} {max_wind:>7.0f}  {dom_dir:>4}  {rating}")

    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    spots = load_spots()
    print_header()

    results = []

    for spot in spots:
        try:
            data = fetch_forecast(spot["lat"], spot["lon"])
            results.append((spot, data))
            print_spot_forecast(spot, data)
        except requests.RequestException as e:
            print(f"\n  ❌ Failed to fetch data for {spot['name']}: {e}")
        except (KeyError, IndexError) as e:
            print(f"\n  ❌ Unexpected data format for {spot['name']}: {e}")

    if results:
        print_best_days_summary(results)

    print("  Data: Open-Meteo.com (NOAA GFS, DWD ICON, ECMWF)")
    print()


if __name__ == "__main__":
    main()
