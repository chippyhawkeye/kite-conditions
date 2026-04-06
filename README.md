# 🪁 Kite Conditions

**7-day kiteboarding wind & weather forecast for your favorite spots.**

Pulls free, publicly available weather data from [Open-Meteo](https://open-meteo.com/) and filters it to **daylight hours only** — so you see exactly when conditions are rideable.

## What You Get

For each spot, for each day over the next 7 days:
- 🌬️ Wind speed & gusts (mph)
- 🧭 Wind direction
- 🌡️ Temperature & feels-like (°F)
- ☀️ Sunrise / sunset times
- ✅ **Kiteability rating** — a quick green/yellow/red assessment

Only daylight hours are shown — no one's kiting at 2 AM.

## Quick Start

```bash
# Clone
git clone https://github.com/chippyhawkeye/kite-conditions.git
cd kite-conditions

# Install dependencies
pip install -r requirements.txt

# Run the forecast
python kite_conditions.py
```

## Configure Your Spots

Edit `spots.json` to add or change locations:

```json
[
  {
    "name": "Cape Hatteras, NC",
    "lat": 35.2225,
    "lon": -75.6350
  }
]
```

Each spot just needs a name and coordinates. You can find coordinates on Google Maps (right-click → copy coordinates).

## Kiteability Rating

The rating is based on wind speed at 10m:

| Rating | Wind (mph) | Meaning |
|--------|-----------|---------|
| 🟢 SEND IT | 15–30 | Ideal kiting conditions |
| 🟡 MAYBE | 12–15 or 30–35 | Marginal — light wind or overpowered |
| 🔴 NOPE | < 12 or > 35 | Too light or too gnarly |

## Data Source

All data comes from the [Open-Meteo API](https://open-meteo.com/) — free, no API key required, powered by national weather services worldwide (NOAA GFS/HRRR, DWD ICON, ECMWF, etc.).

### Why not Windy?

Windy has a Point Forecast API, but the free tier returns **randomly shuffled data** (it's a testing-only key). Their production tier is €990/year. Open-Meteo provides the same underlying model data for free.

## License

MIT
