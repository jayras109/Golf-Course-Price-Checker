import requests
from datetime import datetime, date


def get_forecast(lat: float, lng: float, days: int = 7) -> dict:
    """
    Fetch an Open-Meteo forecast (free, no API key).
    Returns the raw response dict which includes:
      hourly: time, temperature_2m, precipitation_probability, windspeed_10m, winddirection_10m
      daily:  time, sunrise, sunset
      timezone, utc_offset_seconds
    """
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lng,
            "hourly": "temperature_2m,precipitation_probability,windspeed_10m,winddirection_10m",
            "daily": "sunrise,sunset",
            "temperature_unit": "fahrenheit",
            "windspeed_unit": "mph",
            "forecast_days": min(days, 16),
            "timezone": "auto",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_weather_at_time(forecast: dict, dt: datetime) -> dict:
    """
    Return weather conditions at the nearest hour to dt.
    Keys: temp_f, rain_chance, wind_speed, wind_dir
    """
    times = forecast["hourly"]["time"]
    target = dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")

    try:
        idx = times.index(target)
    except ValueError:
        return {"temp_f": None, "rain_chance": None, "wind_speed": None, "wind_dir": None}

    deg = forecast["hourly"]["winddirection_10m"][idx]
    return {
        "temp_f": forecast["hourly"]["temperature_2m"][idx],
        "rain_chance": forecast["hourly"]["precipitation_probability"][idx],
        "wind_speed": forecast["hourly"]["windspeed_10m"][idx],
        "wind_dir": _deg_to_cardinal(deg),
    }


def get_sun_times(forecast: dict, target_date: date) -> tuple:
    """Return (sunrise_dt, sunset_dt) as naive datetimes in the forecast's local timezone."""
    dates = forecast["daily"]["time"]
    date_str = target_date.isoformat()

    if date_str not in dates:
        raise ValueError(f"Date {date_str} not in forecast range")

    idx = dates.index(date_str)
    sunrise = datetime.fromisoformat(forecast["daily"]["sunrise"][idx])
    sunset = datetime.fromisoformat(forecast["daily"]["sunset"][idx])
    return sunrise, sunset


def _deg_to_cardinal(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]
