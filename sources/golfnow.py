"""
GolfNow tee time fetcher using Playwright.

Strategy: launch a headless Chromium browser, navigate to GolfNow's search page
with the right parameters in the URL hash, and intercept every JSON response
that looks like tee time or facility data. GolfNow's SPA makes XHR/fetch calls
to their internal API; we capture those calls and parse whatever comes back.

Because GolfNow's internal API is undocumented, the parser tries many possible
field names. Run with --debug to see exactly which URLs are intercepted and what
the raw JSON looks like, so you can adjust if the structure changes.
"""

import asyncio
import json
import logging
from datetime import date, datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

from models import TeeTime

logger = logging.getLogger(__name__)

_KEYWORDS = ["tee", "course", "search", "facility", "booking", "golf", "rate"]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_tee_times(
    lat: float,
    lng: float,
    radius_miles: int,
    search_date: date,
    group_size: int,
    debug: bool = False,
) -> list:
    """Synchronous wrapper — returns list[TeeTime]."""
    raw = asyncio.run(_scrape(lat, lng, radius_miles, search_date, debug))
    tee_times = _parse_all(raw, lat, lng, group_size)

    # Deduplicate by ID
    seen: set = set()
    unique = []
    for tt in tee_times:
        if tt.id not in seen:
            seen.add(tt.id)
            unique.append(tt)

    logger.info(f"GolfNow returned {len(unique)} unique tee times for {search_date}")
    return unique


# ── Playwright scraping ────────────────────────────────────────────────────────

async def _scrape(lat: float, lng: float, radius_miles: int, search_date: date, debug: bool) -> list:
    from playwright.async_api import async_playwright

    date_str = search_date.strftime("%Y%m%d")
    url = (
        "https://www.golfnow.com/tee-times/search"
        f"#sortby=Date&view=table&holes=all&time=all"
        f"&lat={lat}&lng={lng}&radius={radius_miles}"
        f"&date={date_str}&bookable=true"
    )

    captured: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_USER_AGENT)
        page = await ctx.new_page()

        async def on_response(response):
            if response.status != 200:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            url_lower = response.url.lower()
            if not any(k in url_lower for k in _KEYWORDS):
                return
            try:
                body = await response.body()
                data = json.loads(body)
                captured.append({"url": response.url, "data": data})
                if debug:
                    logger.debug(f"[GolfNow] captured JSON from: {response.url}")
            except Exception as exc:
                if debug:
                    logger.debug(f"[GolfNow] could not parse {response.url}: {exc}")

        page.on("response", on_response)

        logger.info(f"Opening GolfNow for {search_date} (radius {radius_miles} mi)…")
        try:
            await page.goto(url, wait_until="networkidle", timeout=45_000)
        except Exception:
            pass  # networkidle can time out on slow pages; we still captured responses
        await asyncio.sleep(4)  # let any late XHR calls finish

        if debug:
            html = await page.content()
            fname = f"debug_golfnow_{search_date}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            logger.debug(f"[GolfNow] saved page HTML to {fname}")
            logger.debug(f"[GolfNow] total JSON responses captured: {len(captured)}")
            for c in captured:
                logger.debug(f"  {c['url']}")

        await ctx.close()
        await browser.close()

    return captured


# ── Parsing ────────────────────────────────────────────────────────────────────

def _parse_all(captured: list[dict], user_lat: float, user_lng: float, group_size: int) -> list:
    results = []
    for item in captured:
        results.extend(_try_parse(item["data"], item["url"], user_lat, user_lng, group_size))
    return results


def _try_parse(data, url: str, user_lat: float, user_lng: float, group_size: int) -> list:
    if isinstance(data, dict):
        # Try common top-level container keys
        for key in ("Facilities", "facilities", "Courses", "courses", "Results", "results", "data", "items"):
            if key in data and isinstance(data[key], list) and data[key]:
                return _parse_facility_list(data[key], user_lat, user_lng, group_size)

        # Single facility with embedded tee times
        if _has_tee_times_key(data):
            return _parse_facility(data, user_lat, user_lng, group_size)

    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if _has_tee_times_key(first):
                return _parse_facility_list(data, user_lat, user_lng, group_size)
            # Flat list of tee times
            return _parse_flat_list(data, user_lat, user_lng, group_size)

    return []


def _has_tee_times_key(obj: dict) -> bool:
    return any(k in obj for k in ("TeeTimes", "teeTimes", "tee_times", "TeeSheets", "teeSheets", "slots"))


def _parse_facility_list(facilities: list, user_lat, user_lng, group_size) -> list:
    result = []
    for f in facilities:
        result.extend(_parse_facility(f, user_lat, user_lng, group_size))
    return result


def _parse_facility(f: dict, user_lat: float, user_lng: float, group_size: int) -> list:
    course_id = str(_get(f, "Id", "id", "FacilityId", "facilityId", "courseId", "CourseId", default="0"))
    course_name = _get(f, "Name", "name", "FacilityName", "facilityName", "CourseName", "courseName", default="Unknown Course")

    addr = ", ".join(filter(None, [
        _get(f, "Address", "address", "Address1", default=""),
        _get(f, "City", "city", default=""),
        _get(f, "State", "state", "StateProvince", default=""),
    ]))

    c_lat = float(_get(f, "Latitude", "latitude", "Lat", "lat", default=user_lat))
    c_lng = float(_get(f, "Longitude", "longitude", "Lng", "lng", default=user_lng))
    c_type = _get(f, "Type", "type", "CourseType", "courseType", "FacilityType", default="Public")
    rating = _get(f, "Rating", "rating", "StarRating", "starRating", "GoogleRating")
    slope = _get(f, "Slope", "slope", "SlopeRating", "slopeRating")
    dist = _haversine_miles(user_lat, user_lng, c_lat, c_lng)

    tee_times_raw = _get(f, "TeeTimes", "teeTimes", "tee_times", "TeeSheets", "teeSheets", "slots", default=[])

    result = []
    for raw in tee_times_raw:
        tt = _parse_slot(raw, course_id, course_name, addr, c_lat, c_lng, c_type, rating, slope, dist, group_size)
        if tt:
            result.append(tt)
    return result


def _parse_flat_list(items: list, user_lat: float, user_lng: float, group_size: int) -> list:
    result = []
    for raw in items:
        course_id = str(_get(raw, "FacilityId", "facilityId", "CourseId", "courseId", default="0"))
        course_name = _get(raw, "FacilityName", "facilityName", "CourseName", "courseName", default="Unknown")
        addr = ", ".join(filter(None, [
            _get(raw, "Address", "address", default=""),
            _get(raw, "City", "city", default=""),
            _get(raw, "State", "state", default=""),
        ]))
        c_lat = float(_get(raw, "Latitude", "latitude", default=user_lat))
        c_lng = float(_get(raw, "Longitude", "longitude", default=user_lng))
        c_type = _get(raw, "Type", "type", "CourseType", default="Public")
        rating = _get(raw, "Rating", "rating")
        slope = _get(raw, "Slope", "slope")
        dist = _haversine_miles(user_lat, user_lng, c_lat, c_lng)

        tt = _parse_slot(raw, course_id, course_name, addr, c_lat, c_lng, c_type, rating, slope, dist, group_size)
        if tt:
            result.append(tt)
    return result


def _parse_slot(
    raw: dict,
    course_id: str,
    course_name: str,
    course_address: str,
    c_lat: float,
    c_lng: float,
    c_type: str,
    rating,
    slope,
    dist: float,
    group_size: int,
) -> Optional[TeeTime]:

    # ── datetime ──────────────────────────────────────────────────────────────
    time_raw = _get(raw, "StartTime", "startTime", "TeeTime", "teeTime",
                    "DateTime", "dateTime", "Date", "date", "time", "SlotTime", "slotTime")
    if not time_raw:
        return None

    tee_dt = _parse_dt(str(time_raw))
    if tee_dt is None:
        return None

    # ── holes ─────────────────────────────────────────────────────────────────
    holes = int(_get(raw, "Holes", "holes", "NumberOfHoles", "numberOfHoles", default=18))

    # ── players ───────────────────────────────────────────────────────────────
    max_players = int(_get(raw, "MaxPlayers", "maxPlayers", "Players", "players",
                           "MaxGolfers", "maxGolfers", "Capacity", "capacity", default=4))
    available_spots = int(_get(raw, "AvailablePlayers", "availablePlayers",
                               "SpotsAvailable", "spotsAvailable",
                               "Available", "available", default=max_players))

    # ── cart ──────────────────────────────────────────────────────────────────
    cart_raw = _get(raw, "CartIncluded", "cartIncluded", "IncludesCart", "includesCart",
                    "Cart", "cart", "CartRental", "cartRental", default=False)
    if isinstance(cart_raw, str):
        cart_included = cart_raw.lower() in ("true", "yes", "1", "included")
    else:
        cart_included = bool(cart_raw)

    # ── price ─────────────────────────────────────────────────────────────────
    price_raw = float(_get(raw, "Price", "price", "Rate", "rate",
                           "GreenFee", "greenFee", "TotalPrice", "totalPrice",
                           "Amount", "amount", default=0.0) or 0.0)
    price_per_player_field = _get(raw, "PricePerPlayer", "pricePerPlayer", "PlayerRate", "playerRate")
    price_per_group_field = _get(raw, "PricePerGroup", "pricePerGroup", "GroupRate", "groupRate")
    cart_fee = float(_get(raw, "CartFee", "cartFee", "CartPrice", "cartPrice", default=0.0) or 0.0)

    if price_per_player_field is not None:
        price_per_player = float(price_per_player_field)
        is_per_group = False
    elif price_per_group_field is not None:
        price_per_player = float(price_per_group_field) / max(group_size, 1)
        is_per_group = True
    elif price_raw > 250:
        # Heuristic: unusually high single price is likely a group rate
        price_per_player = price_raw / max(group_size, 1)
        is_per_group = True
    else:
        price_per_player = price_raw
        is_per_group = False

    # Add cart fee if cart not already in the price
    if not cart_included and cart_fee > 0:
        price_per_player += cart_fee
        cart_included = True  # fee is now baked in

    # ── booking URL ───────────────────────────────────────────────────────────
    booking_url = _get(raw, "BookingUrl", "bookingUrl", "Url", "url",
                       "Link", "link", "HRef", "href", default="https://www.golfnow.com")

    tt_id = f"{course_id}_{tee_dt.strftime('%Y%m%d_%H%M')}_{holes}"

    return TeeTime(
        id=tt_id,
        course_name=course_name,
        course_address=course_address,
        course_lat=c_lat,
        course_lng=c_lng,
        course_type=str(c_type),
        course_rating=float(rating) if rating is not None else None,
        course_slope=int(slope) if slope is not None else None,
        distance_miles=round(dist, 1),
        tee_datetime=tee_dt,
        holes=holes,
        max_players=max_players,
        available_spots=available_spots,
        cart_included=cart_included,
        price_per_player=round(price_per_player, 2),
        price_raw=price_raw,
        price_is_per_group=is_per_group,
        booking_url=str(booking_url),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(obj: dict, *keys, default=None):
    """Return the first matching key's value from obj."""
    for k in keys:
        if k in obj:
            return obj[k]
    return default


_DT_FMTS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M %p",
]


def _parse_dt(s: str) -> Optional[datetime]:
    s = s.split("+")[0].split("Z")[0].strip()
    for fmt in _DT_FMTS:
        try:
            return datetime.strptime(s[:len(fmt)], fmt)
        except ValueError:
            continue
    return None


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3_958.8
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
