from datetime import timedelta

from models import TeeTime
from sources.weather import get_sun_times, get_weather_at_time


def passes_filters(tt: TeeTime, config: dict, forecast: dict) -> tuple:
    """
    Apply all user-configured filters to a tee time.
    Returns (passed: bool, reasons: list[str]).
    Populates weather fields on tt as a side effect.
    """
    failures = []

    # ── Attach weather ─────────────────────────────────────────────────────────
    wx = get_weather_at_time(forecast, tt.tee_datetime)
    tt.temp_f = wx["temp_f"]
    tt.rain_chance = wx["rain_chance"]
    tt.wind_speed = wx["wind_speed"]
    tt.wind_dir = wx["wind_dir"]

    # ── Sun window: sunrise+1h  →  sunset-2h ──────────────────────────────────
    try:
        sunrise, sunset = get_sun_times(forecast, tt.tee_datetime.date())
        window_open = sunrise + timedelta(hours=1)
        window_close = sunset - timedelta(hours=2)
        tee_naive = tt.tee_datetime.replace(tzinfo=None)

        if not (window_open.replace(tzinfo=None) <= tee_naive <= window_close.replace(tzinfo=None)):
            failures.append(
                f"outside sun window "
                f"({window_open.strftime('%I:%M %p').lstrip('0')} – "
                f"{window_close.strftime('%I:%M %p').lstrip('0')})"
            )
    except Exception:
        pass  # don't filter if sun data is unavailable

    # ── Rain ───────────────────────────────────────────────────────────────────
    rain_limit = config["weather"]["max_rain_chance"]
    if tt.rain_chance is not None and tt.rain_chance > rain_limit:
        failures.append(f"rain {tt.rain_chance:.0f}% > {rain_limit}%")

    # ── Temperature ────────────────────────────────────────────────────────────
    min_t = config["weather"]["min_temp_f"]
    max_t = config["weather"]["max_temp_f"]
    if tt.temp_f is not None:
        if tt.temp_f < min_t:
            failures.append(f"temp {tt.temp_f:.0f}°F < {min_t}°F")
        elif tt.temp_f > max_t:
            failures.append(f"temp {tt.temp_f:.0f}°F > {max_t}°F")

    # ── Cart required ──────────────────────────────────────────────────────────
    if not tt.cart_included:
        failures.append("no cart available")

    # ── Enough spots for the group (not a forced foursome) ────────────────────
    group_size = config.get("group_size", 2)
    if tt.available_spots < group_size:
        failures.append(f"only {tt.available_spots} spot(s), need {group_size}")

    # ── Price per player (cart included) ───────────────────────────────────────
    thresholds = config.get("price_thresholds", {})
    key = f"holes_{tt.holes}"
    limit = thresholds.get(key)
    if limit is not None and tt.price_per_player > limit:
        failures.append(f"${tt.price_per_player:.2f}/player > ${limit:.2f} limit for {tt.holes}-hole")

    # ── Course must be public or semi-private ──────────────────────────────────
    ct = (tt.course_type or "").lower()
    if "private" in ct and "semi" not in ct:
        failures.append("private course")

    # ── Distance ───────────────────────────────────────────────────────────────
    max_dist = config.get("max_distance_miles")
    if max_dist and tt.distance_miles > max_dist:
        failures.append(f"{tt.distance_miles} mi > {max_dist} mi radius")

    return len(failures) == 0, failures
