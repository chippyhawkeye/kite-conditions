#!/usr/bin/env python3
"""
Kite Conditions — Web Dashboard

Serves the 7-day kiteboarding forecast as a responsive website
using Flask. Reuses the same Open-Meteo data pipeline as the CLI tool.
"""

import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from threading import Lock

import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for

# ── Configuration ────────────────────────────────────────────────────────────

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
SPOTS_FILE = _DATA_DIR / "spots.json"
_DEFAULT_SPOTS = Path(__file__).parent / "spots.json"

# On first run with a custom DATA_DIR, copy the default spots if none exist
if not SPOTS_FILE.exists() and _DEFAULT_SPOTS.exists() and SPOTS_FILE != _DEFAULT_SPOTS:
    import shutil
    shutil.copy2(_DEFAULT_SPOTS, SPOTS_FILE)

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

WIND_IDEAL_MIN = 15
WIND_IDEAL_MAX = 30
WIND_MARGINAL_MIN = 12
WIND_MARGINAL_MAX = 35

# In-memory cache: key = (lat, lon) -> (timestamp, data)
# Always fetches full 16-day forecast; date range sliced in build_forecast_data
_forecast_cache = {}
_cache_lock = Lock()
CACHE_TTL = 900  # 15 minutes

WMO_CODES = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Light Showers", 81: "Showers", 82: "Heavy Showers",
    95: "Thunderstorm", 96: "T-storm + Hail", 99: "T-storm + Heavy Hail",
}

WMO_ICONS = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌧️", 55: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}

COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def degrees_to_compass(deg):
    if deg is None:
        return "?"
    idx = round(deg / 22.5) % 16
    return COMPASS[idx]


def kite_rating(wind_mph):
    """Rate a single hour's wind for dot color."""
    if wind_mph is None:
        return "unknown"
    if WIND_IDEAL_MIN <= wind_mph <= WIND_IDEAL_MAX:
        return "send-it"
    elif WIND_MARGINAL_MIN <= wind_mph < WIND_IDEAL_MIN:
        return "maybe"
    elif WIND_IDEAL_MAX < wind_mph <= WIND_MARGINAL_MAX:
        return "maybe"
    else:
        return "nope"


def day_kite_rating(daylight_winds, daylight_gusts):
    """Rate an entire day based on hourly wind consistency and gust factor.

    Philosophy:
    - Base wind speed (not gusts) determines if you can kite
    - SEND IT = every daylight hour has rideable wind AND gusts are tame
    - MAYBE  = most hours rideable but some weak/strong periods, or gusty
    - NOPE   = not enough consistent wind or dangerously gusty
    """
    if not daylight_winds:
        return "unknown"

    total = len(daylight_winds)
    ideal = sum(1 for w in daylight_winds if WIND_IDEAL_MIN <= w <= WIND_IDEAL_MAX)
    rideable = sum(1 for w in daylight_winds if WIND_MARGINAL_MIN <= w <= WIND_MARGINAL_MAX)
    avg_wind = sum(daylight_winds) / total

    # Gust factor: ratio of max gust to average base wind
    max_gust = max(daylight_gusts) if daylight_gusts else 0
    gust_factor = (max_gust / avg_wind) if avg_wind > 0 else 0

    # NOPE: fewer than half the hours are even rideable, or extreme gusts
    if rideable < total * 0.5 or max_gust > WIND_MARGINAL_MAX:
        return "nope"

    # SEND IT: every hour is ideal range AND gust factor is tame (≤ 1.5)
    if ideal == total and gust_factor <= 1.5:
        return "send-it"

    # MAYBE: decent wind but not perfect consistency or gusty
    if rideable >= total * 0.6:
        return "maybe"

    return "nope"


def rating_label(rating):
    return {"send-it": "SEND IT", "maybe": "MAYBE", "nope": "NOPE", "unknown": "?"}.get(rating, "?")


def rating_emoji(rating):
    return {"send-it": "🟢", "maybe": "🟡", "nope": "🔴", "unknown": "⚪"}.get(rating, "⚪")


def weather_desc(code):
    return WMO_CODES.get(code, f"Code {code}")


def weather_icon(code):
    return WMO_ICONS.get(code, "🌡️")


def load_spots():
    if not SPOTS_FILE.exists():
        with open(SPOTS_FILE, "w") as f:
            json.dump([], f)
        return []
    with open(SPOTS_FILE) as f:
        spots = json.load(f)
    # Ensure every spot has an id
    changed = False
    for s in spots:
        if "id" not in s:
            s["id"] = str(uuid.uuid4())[:8]
            changed = True
    if changed:
        save_spots(spots)
    return spots


def save_spots(spots):
    with open(SPOTS_FILE, "w") as f:
        json.dump(spots, f, indent=2)
    return spots


def fetch_forecast(lat, lon):
    """Fetch the full 16-day forecast for a location, using cache.

    Returns (data, cache_age_seconds).  cache_age_seconds == 0 means fresh fetch.
    """
    cache_key = (round(lat, 4), round(lon, 4))
    now = time.time()

    with _cache_lock:
        if cache_key in _forecast_cache:
            ts, data = _forecast_cache[cache_key]
            age = now - ts
            if age < CACHE_TTL:
                return data, age

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_PARAMS),
        "daily": ",".join(DAILY_PARAMS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": 16,
    }

    # Retry with exponential backoff (3 attempts: 0s, 2s, 4s)
    last_err = None
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(2 ** attempt)  # 2s, 4s
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
            resp.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            continue
    else:
        raise last_err  # all retries exhausted

    data = resp.json()

    with _cache_lock:
        _forecast_cache[cache_key] = (time.time(), data)
        # Evict stale entries
        stale = [k for k, (ts, _) in _forecast_cache.items() if time.time() - ts > CACHE_TTL * 2]
        for k in stale:
            del _forecast_cache[k]

    return data, 0


def _fetch_spot(spot, stagger=0):
    """Fetch full forecast for one spot. Returns (spot, data, error, cache_age)."""
    try:
        if stagger > 0:
            time.sleep(stagger)
        data, cache_age = fetch_forecast(spot["lat"], spot["lon"])
        return (spot, data, None, cache_age)
    except Exception as e:
        return (spot, None, str(e), None)


# ── Data Processing ──────────────────────────────────────────────────────────

def build_forecast_data(start_date=None, end_date=None):
    """Fetch and structure all forecast data for the template."""
    spots = load_spots()
    generated = datetime.now().strftime("%A, %B %d %Y at %I:%M %p")

    all_spots = []
    best_days = []

    # Parse requested date range for filtering
    req_dates = set()
    if start_date and end_date:
        d = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        while d <= end_d:
            req_dates.add(d.isoformat())
            d += timedelta(days=1)

    # Fetch all spots in parallel (full 16-day forecast, cached)
    results = []
    cache_ages = []
    if spots:
        with ThreadPoolExecutor(max_workers=min(len(spots), 4)) as executor:
            futures = {executor.submit(_fetch_spot, spot, i * 0.3): spot for i, spot in enumerate(spots)}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if result[3] is not None:
                    cache_ages.append(result[3])

    # Preserve original spot order
    spot_order = {s["name"]: i for i, s in enumerate(spots)}
    results.sort(key=lambda r: spot_order.get(r[0]["name"], 999))

    # Determine cache freshness for UI display
    if cache_ages:
        max_cache_age = max(cache_ages)
        all_cached = all(a > 0 for a in cache_ages)
    else:
        max_cache_age = 0
        all_cached = False

    for spot, data, error, _age in results:
        if error:
            all_spots.append({
                "id": spot.get("id", ""),
                "name": spot["name"],
                "lat": spot["lat"],
                "lon": spot["lon"],
                "error": error,
                "days": [],
            })
            continue

        tz = data.get("timezone", "UTC")
        hourly = data["hourly"]
        daily = data["daily"]
        times = hourly["time"]

        # Group hours by date, filtering to requested range
        days_map = {}
        for i, t in enumerate(times):
            date_str = t[:10]
            if req_dates and date_str not in req_dates:
                continue
            if date_str not in days_map:
                days_map[date_str] = []
            days_map[date_str].append(i)

        # Build a lookup from date string to daily array index
        daily_dates = daily.get("time", [])
        daily_idx_map = {d: i for i, d in enumerate(daily_dates)}

        spot_days = []
        for date_str, hour_indices in days_map.items():
            day_idx = daily_idx_map.get(date_str)
            dt = datetime.strptime(date_str, "%Y-%m-%d")

            if day_idx is not None:
                sunrise = daily["sunrise"][day_idx][-5:] if day_idx < len(daily["sunrise"]) else "?"
                sunset = daily["sunset"][day_idx][-5:] if day_idx < len(daily["sunset"]) else "?"
                hi = daily["temperature_2m_max"][day_idx] if day_idx < len(daily["temperature_2m_max"]) else None
                lo = daily["temperature_2m_min"][day_idx] if day_idx < len(daily["temperature_2m_min"]) else None
                max_wind = daily["wind_speed_10m_max"][day_idx] if day_idx < len(daily["wind_speed_10m_max"]) else None
                max_gust = daily["wind_gusts_10m_max"][day_idx] if day_idx < len(daily["wind_gusts_10m_max"]) else None
                dom_dir_deg = daily["wind_direction_10m_dominant"][day_idx] if day_idx < len(daily["wind_direction_10m_dominant"]) else None
            else:
                sunrise = sunset = "?"
                hi = lo = max_wind = max_gust = dom_dir_deg = None

            hours = []
            daylight_winds = []
            daylight_gusts = []
            daylight_temps = []
            for i in hour_indices:
                if not hourly["is_day"][i]:
                    continue
                wind = hourly["wind_speed_10m"][i]
                gust = hourly["wind_gusts_10m"][i]
                temp = hourly["temperature_2m"][i]
                if wind is not None:
                    daylight_winds.append(wind)
                if gust is not None:
                    daylight_gusts.append(gust)
                if temp is not None:
                    daylight_temps.append(temp)
                hours.append({
                    "time": times[i][-5:],
                    "temp": round(hourly["temperature_2m"][i]) if hourly["temperature_2m"][i] is not None else None,
                    "feels": round(hourly["apparent_temperature"][i]) if hourly["apparent_temperature"][i] is not None else None,
                    "wind": round(wind) if wind is not None else None,
                    "gust": round(gust) if gust is not None else None,
                    "dir": degrees_to_compass(hourly["wind_direction_10m"][i]),
                    "sky": weather_desc(hourly["weather_code"][i]),
                    "sky_icon": weather_icon(hourly["weather_code"][i]),
                    "rating": kite_rating(wind),
                })

            avg_wind = round(sum(daylight_winds) / len(daylight_winds)) if daylight_winds else None
            avg_temp = round(sum(daylight_temps) / len(daylight_temps)) if daylight_temps else None

            # Daily rating: based on hourly consistency + gust factor
            day_rating = day_kite_rating(daylight_winds, daylight_gusts)
            gust_factor = None
            gust_pct = None  # percentage above base wind
            if avg_wind and daylight_gusts:
                gust_factor = round(max(daylight_gusts) / (sum(daylight_winds) / len(daylight_winds)), 1)
                gust_pct = round((gust_factor - 1) * 100)

            day_data = {
                "date": date_str,
                "day_name": dt.strftime("%A"),
                "day_short": dt.strftime("%a"),
                "month_day": dt.strftime("%b %d"),
                "sunrise": sunrise,
                "sunset": sunset,
                "hi": round(hi) if hi is not None else None,
                "lo": round(lo) if lo is not None else None,
                "avg_temp": avg_temp,
                "max_wind": round(max_wind) if max_wind is not None else None,
                "avg_wind": avg_wind,
                "max_gust": round(max_gust) if max_gust is not None else None,
                "gust_factor": gust_factor,
                "gust_pct": gust_pct,
                "dom_dir": degrees_to_compass(dom_dir_deg),
                "rating": day_rating,
                "rating_label": rating_label(day_rating),
                "rating_emoji": rating_emoji(day_rating),
                "hours": hours,
            }
            spot_days.append(day_data)

            # Best days summary
            if day_rating in ("send-it", "maybe"):
                best_days.append({
                    "spot": spot["name"],
                    "day_name": dt.strftime("%a %b %d"),
                    "max_wind": round(max_wind) if max_wind is not None else None,
                    "dom_dir": degrees_to_compass(dom_dir_deg),
                    "rating": day_rating,
                    "rating_label": rating_label(day_rating),
                    "rating_emoji": rating_emoji(day_rating),
                })

        all_spots.append({
            "id": spot.get("id", ""),
            "name": spot["name"],
            "lat": spot["lat"],
            "lon": spot["lon"],
            "timezone": tz,
            "days": spot_days,
        })

    # Sort spots by overall average wind (best conditions first)
    for s in all_spots:
        winds = [d["avg_wind"] for d in s["days"] if d.get("avg_wind") is not None]
        s["overall_avg_wind"] = round(sum(winds) / len(winds)) if winds else 0
    all_spots.sort(key=lambda s: s["overall_avg_wind"], reverse=True)

    # Build day-centric grid: for each date, gather all spots' daily data
    # Collect all unique dates in order from the first spot that has data
    day_dates = []
    for s in all_spots:
        if s["days"]:
            day_dates = [d["date"] for d in s["days"]]
            break

    days_grid = []
    for date_str in day_dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_spots = []
        for s in all_spots:
            # Find this date in the spot's days
            match = next((d for d in s["days"] if d["date"] == date_str), None)
            if match:
                day_spots.append({
                    "id": s.get("id", ""),
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    **match,
                })
            else:
                day_spots.append({
                    "id": s.get("id", ""),
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "rating": "unknown",
                    "rating_label": "?",
                    "rating_emoji": "⚪",
                    "max_wind": None,
                    "avg_wind": None,
                    "max_gust": None,
                    "gust_factor": None,
                    "gust_pct": None,
                    "avg_temp": None,
                    "dom_dir": "?",
                    "hi": None,
                    "lo": None,
                    "sunrise": "?",
                    "sunset": "?",
                    "hours": [],
                })
        days_grid.append({
            "date": date_str,
            "day_name": dt.strftime("%A"),
            "day_short": dt.strftime("%a"),
            "month_day": dt.strftime("%b %d"),
            "spots": day_spots,
        })

    return {
        "generated": generated,
        "spots": all_spots,
        "best_days": best_days,
        "days_grid": days_grid,
        "cache_age": round(max_cache_age),
        "all_cached": all_cached,
    }


# ── Flask App ────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    today = date.today()
    max_days_out = 16  # Open-Meteo supports up to 16 days

    # Accept offset (days from today) and days (duration) params
    try:
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0
    try:
        num_days = int(request.args.get("days", 7))
    except (ValueError, TypeError):
        num_days = 7

    # Clamp to valid range
    offset = max(0, min(offset, max_days_out - 1))
    num_days = max(1, min(num_days, max_days_out - offset))

    start_date = today + timedelta(days=offset)
    end_date = start_date + timedelta(days=num_days - 1)

    forecast = build_forecast_data(start_date.isoformat(), end_date.isoformat())
    forecast["num_days"] = num_days
    forecast["start_offset"] = offset
    forecast["start_date"] = start_date.isoformat()
    forecast["end_date"] = end_date.isoformat()
    forecast["today"] = today.isoformat()
    forecast["max_end"] = (today + timedelta(days=max_days_out - 1)).isoformat()
    return render_template("index.html", **forecast)


@app.route("/spot/<spot_id>")
def spot_detail(spot_id):
    """Detail page for a single spot with current conditions + day-by-day forecast."""
    spots = load_spots()
    spot = next((s for s in spots if s["id"] == spot_id), None)
    if not spot:
        return redirect(url_for("index"))

    today = date.today()
    max_days_out = 16

    try:
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0
    try:
        num_days = int(request.args.get("days", 7))
    except (ValueError, TypeError):
        num_days = 7

    offset = max(0, min(offset, max_days_out - 1))
    num_days = max(1, min(num_days, max_days_out - offset))

    start_date = today + timedelta(days=offset)
    end_date = start_date + timedelta(days=num_days - 1)

    # Fetch forecast for this single spot (uses cache)
    try:
        raw_data, cache_age = fetch_forecast(spot["lat"], spot["lon"])
        error = None
    except Exception as e:
        raw_data = None
        cache_age = 0
        error = str(e)

    # Process into the same structure as build_forecast_data for one spot
    spot_data = {
        "name": spot["name"],
        "lat": spot["lat"],
        "lon": spot["lon"],
        "id": spot["id"],
        "error": error,
        "days": [],
        "timezone": "",
        "webcams": spot.get("webcams", []),
    }

    # Current conditions placeholder
    current = None

    if raw_data and not error:
        tz = raw_data.get("timezone", "UTC")
        spot_data["timezone"] = tz
        hourly = raw_data["hourly"]
        daily = raw_data["daily"]
        times = hourly["time"]

        # Build requested date set
        req_dates = set()
        d = start_date
        while d <= end_date:
            req_dates.add(d.isoformat())
            d += timedelta(days=1)

        # Find "now" hour for current conditions (today, closest past hour)
        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        current_idx = None
        for i, t in enumerate(times):
            if t <= now_str:
                current_idx = i
        if current_idx is not None:
            current = {
                "time": times[current_idx][-5:],
                "temp": round(hourly["temperature_2m"][current_idx]) if hourly["temperature_2m"][current_idx] is not None else None,
                "feels": round(hourly["apparent_temperature"][current_idx]) if hourly["apparent_temperature"][current_idx] is not None else None,
                "wind": round(hourly["wind_speed_10m"][current_idx]) if hourly["wind_speed_10m"][current_idx] is not None else None,
                "gust": round(hourly["wind_gusts_10m"][current_idx]) if hourly["wind_gusts_10m"][current_idx] is not None else None,
                "dir": degrees_to_compass(hourly["wind_direction_10m"][current_idx]),
                "sky": weather_desc(hourly["weather_code"][current_idx]),
                "sky_icon": weather_icon(hourly["weather_code"][current_idx]),
                "rating": kite_rating(hourly["wind_speed_10m"][current_idx]),
                "is_day": hourly["is_day"][current_idx],
            }

        # Group hours by date, filter to requested range
        days_map = {}
        for i, t in enumerate(times):
            date_str = t[:10]
            if date_str not in req_dates:
                continue
            if date_str not in days_map:
                days_map[date_str] = []
            days_map[date_str].append(i)

        daily_dates = daily.get("time", [])
        daily_idx_map = {d: i for i, d in enumerate(daily_dates)}

        for date_str, hour_indices in days_map.items():
            day_idx = daily_idx_map.get(date_str)
            dt = datetime.strptime(date_str, "%Y-%m-%d")

            if day_idx is not None:
                sunrise = daily["sunrise"][day_idx][-5:] if day_idx < len(daily["sunrise"]) else "?"
                sunset = daily["sunset"][day_idx][-5:] if day_idx < len(daily["sunset"]) else "?"
                hi = daily["temperature_2m_max"][day_idx] if day_idx < len(daily["temperature_2m_max"]) else None
                lo = daily["temperature_2m_min"][day_idx] if day_idx < len(daily["temperature_2m_min"]) else None
                max_wind = daily["wind_speed_10m_max"][day_idx] if day_idx < len(daily["wind_speed_10m_max"]) else None
                max_gust = daily["wind_gusts_10m_max"][day_idx] if day_idx < len(daily["wind_gusts_10m_max"]) else None
                dom_dir_deg = daily["wind_direction_10m_dominant"][day_idx] if day_idx < len(daily["wind_direction_10m_dominant"]) else None
            else:
                sunrise = sunset = "?"
                hi = lo = max_wind = max_gust = dom_dir_deg = None

            hours = []
            daylight_winds = []
            daylight_gusts = []
            daylight_temps = []
            for i in hour_indices:
                if not hourly["is_day"][i]:
                    continue
                wind = hourly["wind_speed_10m"][i]
                gust = hourly["wind_gusts_10m"][i]
                temp = hourly["temperature_2m"][i]
                if wind is not None:
                    daylight_winds.append(wind)
                if gust is not None:
                    daylight_gusts.append(gust)
                if temp is not None:
                    daylight_temps.append(temp)
                hours.append({
                    "time": times[i][-5:],
                    "temp": round(temp) if temp is not None else None,
                    "feels": round(hourly["apparent_temperature"][i]) if hourly["apparent_temperature"][i] is not None else None,
                    "wind": round(wind) if wind is not None else None,
                    "gust": round(gust) if gust is not None else None,
                    "dir": degrees_to_compass(hourly["wind_direction_10m"][i]),
                    "sky": weather_desc(hourly["weather_code"][i]),
                    "sky_icon": weather_icon(hourly["weather_code"][i]),
                    "rating": kite_rating(wind),
                })

            avg_wind = round(sum(daylight_winds) / len(daylight_winds)) if daylight_winds else None
            avg_temp = round(sum(daylight_temps) / len(daylight_temps)) if daylight_temps else None
            day_rating = day_kite_rating(daylight_winds, daylight_gusts)
            gust_factor = None
            gust_pct = None
            if avg_wind and daylight_gusts:
                gust_factor = round(max(daylight_gusts) / (sum(daylight_winds) / len(daylight_winds)), 1)
                gust_pct = round((gust_factor - 1) * 100)

            spot_data["days"].append({
                "date": date_str,
                "day_name": dt.strftime("%A"),
                "day_short": dt.strftime("%a"),
                "month_day": dt.strftime("%b %d"),
                "sunrise": sunrise,
                "sunset": sunset,
                "hi": round(hi) if hi is not None else None,
                "lo": round(lo) if lo is not None else None,
                "avg_temp": avg_temp,
                "max_wind": round(max_wind) if max_wind is not None else None,
                "avg_wind": avg_wind,
                "max_gust": round(max_gust) if max_gust is not None else None,
                "gust_factor": gust_factor,
                "gust_pct": gust_pct,
                "dom_dir": degrees_to_compass(dom_dir_deg),
                "rating": day_rating,
                "rating_label": rating_label(day_rating),
                "rating_emoji": rating_emoji(day_rating),
                "hours": hours,
            })

    generated = datetime.now().strftime("%A, %B %d %Y at %I:%M %p")

    return render_template("spot.html",
        spot=spot_data,
        current=current,
        generated=generated,
        cache_age=round(cache_age),
        all_cached=cache_age > 0,
        num_days=num_days,
        start_offset=offset,
    )


# ── Location Editor ──────────────────────────────────────────────────────────

@app.route("/settings")
def settings():
    spots = load_spots()
    return render_template("settings.html", spots=spots)


@app.route("/api/spots", methods=["GET"])
def api_get_spots():
    return jsonify(load_spots())


@app.route("/api/spots", methods=["POST"])
def api_add_spot():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    try:
        lat = float(data.get("lat", 0))
        lon = float(data.get("lon", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid lat/lon"}), 400
    if not name:
        return jsonify({"error": "Name is required"}), 400

    spots = load_spots()
    new_spot = {"id": str(uuid.uuid4())[:8], "name": name, "lat": lat, "lon": lon}
    spots.append(new_spot)
    save_spots(spots)
    return jsonify(new_spot), 201


@app.route("/api/spots/<spot_id>", methods=["PUT"])
def api_update_spot(spot_id):
    data = request.get_json(force=True)
    spots = load_spots()
    spot = next((s for s in spots if s["id"] == spot_id), None)
    if not spot:
        return jsonify({"error": "Spot not found"}), 404

    name = data.get("name", "").strip()
    if name:
        spot["name"] = name
    if "lat" in data:
        try:
            spot["lat"] = float(data["lat"])
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid lat"}), 400
    if "lon" in data:
        try:
            spot["lon"] = float(data["lon"])
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid lon"}), 400

    save_spots(spots)
    return jsonify(spot)


@app.route("/api/spots/<spot_id>", methods=["DELETE"])
def api_delete_spot(spot_id):
    spots = load_spots()
    spots = [s for s in spots if s["id"] != spot_id]
    save_spots(spots)
    return jsonify({"ok": True})


@app.route("/api/geocode")
def api_geocode():
    """Geocode a location name using Open-Meteo's free geocoding API."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query required"}), 400
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": q, "count": 5, "language": "en", "format": "json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("results", []):
            label = r.get("name", "")
            parts = [r.get("admin1"), r.get("country")]
            label += ", " + ", ".join(p for p in parts if p)
            results.append({
                "name": label,
                "lat": round(r["latitude"], 4),
                "lon": round(r["longitude"], 4),
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("🪁 Kite Conditions — Web Dashboard")
    print("   http://localhost:5555")
    print("   Press Ctrl+C to quit\n")
    app.run(debug=True, port=5555, threaded=True)
