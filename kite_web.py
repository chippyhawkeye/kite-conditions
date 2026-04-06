#!/usr/bin/env python3
"""
Kite Conditions — Web Dashboard

Serves the 7-day kiteboarding forecast as a responsive website
using Flask. Reuses the same Open-Meteo data pipeline as the CLI tool.
"""

import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
from flask import Flask, render_template, request

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

WIND_IDEAL_MIN = 15
WIND_IDEAL_MAX = 30
WIND_MARGINAL_MIN = 12
WIND_MARGINAL_MAX = 35

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
        print(f"❌ Spots file not found: {SPOTS_FILE}")
        sys.exit(1)
    with open(SPOTS_FILE) as f:
        return json.load(f)


def fetch_forecast(lat, lon, start_date=None, end_date=None):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_PARAMS),
        "daily": ",".join(DAILY_PARAMS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
    }
    if start_date and end_date:
        params["start_date"] = start_date
        params["end_date"] = end_date
    else:
        params["forecast_days"] = 7
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Data Processing ──────────────────────────────────────────────────────────

def build_forecast_data(start_date=None, end_date=None):
    """Fetch and structure all forecast data for the template."""
    spots = load_spots()
    generated = datetime.now().strftime("%A, %B %d %Y at %I:%M %p")

    all_spots = []
    best_days = []

    for spot in spots:
        try:
            data = fetch_forecast(spot["lat"], spot["lon"], start_date, end_date)
        except Exception as e:
            all_spots.append({
                "name": spot["name"],
                "lat": spot["lat"],
                "lon": spot["lon"],
                "error": str(e),
                "days": [],
            })
            continue

        tz = data.get("timezone", "UTC")
        hourly = data["hourly"]
        daily = data["daily"]
        times = hourly["time"]

        # Group hours by date
        days_map = {}
        for i, t in enumerate(times):
            date_str = t[:10]
            if date_str not in days_map:
                days_map[date_str] = []
            days_map[date_str].append(i)

        spot_days = []
        for day_idx, (date_str, hour_indices) in enumerate(days_map.items()):
            dt = datetime.strptime(date_str, "%Y-%m-%d")

            sunrise = daily["sunrise"][day_idx][-5:] if day_idx < len(daily["sunrise"]) else "?"
            sunset = daily["sunset"][day_idx][-5:] if day_idx < len(daily["sunset"]) else "?"
            hi = daily["temperature_2m_max"][day_idx] if day_idx < len(daily["temperature_2m_max"]) else None
            lo = daily["temperature_2m_min"][day_idx] if day_idx < len(daily["temperature_2m_min"]) else None
            max_wind = daily["wind_speed_10m_max"][day_idx] if day_idx < len(daily["wind_speed_10m_max"]) else None
            max_gust = daily["wind_gusts_10m_max"][day_idx] if day_idx < len(daily["wind_gusts_10m_max"]) else None
            dom_dir_deg = daily["wind_direction_10m_dominant"][day_idx] if day_idx < len(daily["wind_direction_10m_dominant"]) else None

            rating = kite_rating(max_wind)

            hours = []
            daylight_winds = []
            for i in hour_indices:
                if not hourly["is_day"][i]:
                    continue
                wind = hourly["wind_speed_10m"][i]
                if wind is not None:
                    daylight_winds.append(wind)
                hours.append({
                    "time": times[i][-5:],
                    "temp": round(hourly["temperature_2m"][i]) if hourly["temperature_2m"][i] is not None else None,
                    "feels": round(hourly["apparent_temperature"][i]) if hourly["apparent_temperature"][i] is not None else None,
                    "wind": round(wind) if wind is not None else None,
                    "gust": round(hourly["wind_gusts_10m"][i]) if hourly["wind_gusts_10m"][i] is not None else None,
                    "dir": degrees_to_compass(hourly["wind_direction_10m"][i]),
                    "sky": weather_desc(hourly["weather_code"][i]),
                    "sky_icon": weather_icon(hourly["weather_code"][i]),
                    "rating": kite_rating(wind),
                })

            avg_wind = round(sum(daylight_winds) / len(daylight_winds)) if daylight_winds else None

            day_data = {
                "date": date_str,
                "day_name": dt.strftime("%A"),
                "day_short": dt.strftime("%a"),
                "month_day": dt.strftime("%b %d"),
                "sunrise": sunrise,
                "sunset": sunset,
                "hi": round(hi) if hi is not None else None,
                "lo": round(lo) if lo is not None else None,
                "max_wind": round(max_wind) if max_wind is not None else None,
                "avg_wind": avg_wind,
                "max_gust": round(max_gust) if max_gust is not None else None,
                "dom_dir": degrees_to_compass(dom_dir_deg),
                "rating": rating,
                "rating_label": rating_label(rating),
                "rating_emoji": rating_emoji(rating),
                "hours": hours,
            }
            spot_days.append(day_data)

            # Best days summary
            if rating in ("send-it", "maybe"):
                best_days.append({
                    "spot": spot["name"],
                    "day_name": dt.strftime("%a %b %d"),
                    "max_wind": round(max_wind) if max_wind is not None else None,
                    "dom_dir": degrees_to_compass(dom_dir_deg),
                    "rating": rating,
                    "rating_label": rating_label(rating),
                    "rating_emoji": rating_emoji(rating),
                })

        all_spots.append({
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
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    **match,
                })
            else:
                day_spots.append({
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                    "rating": "unknown",
                    "rating_label": "?",
                    "rating_emoji": "⚪",
                    "max_wind": None,
                    "avg_wind": None,
                    "max_gust": None,
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
    }


# ── Flask App ────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    # Date range from query params, default to today + 7 days
    today = date.today()
    default_end = today + timedelta(days=6)
    max_end = today + timedelta(days=15)  # Open-Meteo supports up to 16 days

    start_str = request.args.get("start", today.isoformat())
    end_str = request.args.get("end", default_end.isoformat())

    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        start_date = today
        end_date = default_end

    # Clamp to valid range
    if start_date < today:
        start_date = today
    if end_date > max_end:
        end_date = max_end
    if end_date < start_date:
        end_date = start_date

    forecast = build_forecast_data(start_date.isoformat(), end_date.isoformat())
    forecast["start_date"] = start_date.isoformat()
    forecast["end_date"] = end_date.isoformat()
    forecast["today"] = today.isoformat()
    forecast["max_end"] = max_end.isoformat()
    forecast["num_days"] = (end_date - start_date).days + 1
    return render_template("index.html", **forecast)


if __name__ == "__main__":
    print("🪁 Kite Conditions — Web Dashboard")
    print("   http://localhost:5555")
    print("   Press Ctrl+C to quit\n")
    app.run(debug=True, port=5555, threaded=True)
