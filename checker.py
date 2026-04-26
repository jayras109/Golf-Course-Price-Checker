import logging
from datetime import date, timedelta

import yaml

from models import TeeTime
from sources.geocoder import geocode
from sources.weather import get_forecast
from sources.golfnow import fetch_tee_times
from filters import passes_filters
from storage import is_new, mark_alerted, cleanup_old
from notifier import send_email

logger = logging.getLogger(__name__)

_DAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_check(
    config: dict,
    dates: list = None,
    mode: str = "scheduled",
    debug: bool = False,
    print_results: bool = False,
) -> list:
    """
    Core check orchestrator.

    mode
    ----
    'scheduled' — only email tee times not seen today; used by background runner
    'scan'      — email + print all passing tee times regardless of dedup
    'range'     — date-range scan, sort by distance, single summary email
    """
    address = config["address"]
    lat, lng = geocode(address)
    radius = config["max_distance_miles"]
    group_size = config.get("group_size", 2)

    if dates is None:
        if mode == "scan":
            dates = [date.today()]
        else:
            check_days = {_DAY_MAP[str(d).lower()] for d in config["schedule"].get("check_days", ["fri", "sat", "sun"]) if str(d).lower() in _DAY_MAP}
            lookahead = config["schedule"].get("lookahead_days", 7)
            dates = [
                date.today() + timedelta(days=i)
                for i in range(lookahead)
                if (date.today() + timedelta(days=i)).weekday() in check_days
            ]

    if not dates:
        logger.info("No dates to check.")
        return []

    forecast_days = min((max(dates) - date.today()).days + 2, 16)
    forecast_days = max(forecast_days, 7)

    logger.info(f"Fetching weather for {lat:.4f}, {lng:.4f} ({forecast_days} days)…")
    forecast = get_forecast(lat, lng, days=forecast_days)

    all_passing: list[TeeTime] = []

    for check_date in dates:
        logger.info(f"Checking {check_date}…")
        try:
            raw = fetch_tee_times(lat, lng, radius, check_date, group_size, debug=debug)
        except Exception as exc:
            logger.error(f"  Fetch failed for {check_date}: {exc}")
            continue

        passing = []
        for tt in raw:
            try:
                ok, reasons = passes_filters(tt, config, forecast)
                if ok:
                    passing.append(tt)
                elif debug:
                    logger.debug(f"  Filtered: {tt.course_name} @ {tt.tee_datetime} — {reasons}")
            except Exception as exc:
                logger.warning(f"  Filter error ({tt.id}): {exc}")

        logger.info(f"  {len(passing)}/{len(raw)} tee times passed filters")
        all_passing.extend(passing)

    cleanup_old()

    if not all_passing:
        logger.info("No qualifying tee times found.")
        if print_results:
            print("\nNo qualifying tee times found.")
        return []

    if mode == "range":
        all_passing.sort(key=lambda t: t.distance_miles)
        send_email(all_passing, config, mode="range")
        if print_results:
            _print_tee_times(all_passing)

    else:
        to_alert = all_passing if mode == "scan" else [tt for tt in all_passing if is_new(tt.id)]

        if to_alert:
            send_email(to_alert, config, mode="alert")
            if mode == "scheduled":
                mark_alerted([tt.id for tt in to_alert])
        else:
            logger.info("No new tee times since last alert.")

        if print_results:
            _print_tee_times(to_alert or all_passing)

    return all_passing


def _print_tee_times(tee_times: list):
    if not tee_times:
        print("\nNo qualifying tee times.")
        return

    print(f"\n{'=' * 62}")
    print(f"  {len(tee_times)} QUALIFYING TEE TIME{'S' if len(tee_times) != 1 else ''}")
    print(f"{'=' * 62}")

    for tt in sorted(tee_times, key=lambda t: (t.tee_datetime, t.distance_miles)):
        hour = tt.tee_datetime.hour % 12 or 12
        ampm = "AM" if tt.tee_datetime.hour < 12 else "PM"
        time_str = f"{tt.tee_datetime.strftime('%a %b')} {tt.tee_datetime.day} @ {hour}:{tt.tee_datetime.strftime('%M')} {ampm}"

        print(f"\n  {tt.course_name}  ({tt.distance_miles} mi)")
        print(f"  {time_str}  |  {tt.holes} holes  |  ${tt.price_per_player:.2f}/player")
        print(f"  Cart: {'Yes' if tt.cart_included else 'No'}  |  Up to {tt.available_spots} players  |  {tt.course_type}")
        if tt.temp_f is not None:
            print(f"  Weather: {tt.temp_f:.0f}°F  |  Rain: {tt.rain_chance:.0f}%  |  Wind: {tt.wind_speed:.0f} mph {tt.wind_dir or ''}")
        print(f"  {tt.booking_url}")

    print(f"\n{'=' * 62}\n")
