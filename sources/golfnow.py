"""
GolfNow tee time fetcher using Playwright.

Strategy:
1. Launch headless Chromium and navigate to GolfNow's search page.
2. Establish session cookies via the page load.
3. POST directly to /api/tee-times/tee-times-with-inventory with the user's
   lat/lng — this is the endpoint GolfNow uses to show individual tee time
   slots with price/cart/holes data per slot.
4. Parse the response into TeeTime objects.

Run any command with --debug to save the inventory JSON and page HTML.
"""

import asyncio
import json
import logging
from datetime import date, datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

from models import TeeTime

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_MIN_BODY = 200


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
    raw = asyncio.run(_scrape(lat, lng, radius_miles, search_date, group_size, debug))
    tee_times = _parse_all(raw, lat, lng, group_size)

    seen: set = set()
    unique = []
    for tt in tee_times:
        if tt.id not in seen:
            seen.add(tt.id)
            unique.append(tt)

    logger.info(f"GolfNow returned {len(unique)} unique tee times for {search_date}")
    return unique


# ── Playwright scraping ────────────────────────────────────────────────────────

async def _scrape(
    lat: float,
    lng: float,
    radius_miles: int,
    search_date: date,
    group_size: int,
    debug: bool,
) -> list:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    date_str = search_date.strftime("%b %d %Y")   # "Apr 26 2026"
    date_ymd = search_date.strftime("%Y-%m-%d")   # "2026-04-26"

    captured: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await ctx.new_page()

        # ── Step 1: load the search page to establish session cookies ──────────
        logger.info(f"Opening GolfNow search ({search_date}, radius {radius_miles} mi)...")
        try:
            await page.goto(
                "https://www.golfnow.com/tee-times/search",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except PWTimeout:
            logger.warning("[GolfNow] page load timeout -- continuing anyway")

        await asyncio.sleep(3)

        # ── Step 2: POST to tee-times-with-inventory with user's coordinates ───
        # This endpoint returns individual tee time slots (not just facility summaries).
        # GolfNow uses IP geolocation for its own search, so we must pass lat/lng
        # explicitly in the payload to get results near the user's address.
        payload = {
            "pageSize": 30,
            "teeTimeCount": 50,
            "pageNumber": 0,
            "date": date_str,
            "sortBy": "Facilities.Distance",
            "sortByRollup": "Facilities.Distance",
            "sortDirection": "Asc",
            "hotDealsOnly": False,
            "priceMin": 0,
            "priceMax": 10000,
            "players": group_size,
            "timePeriod": 0,
            "timeMin": 0,
            "timeMax": 47,
            "holes": "18",
            "facilityType": "GolfCourse",
            "latitude": str(lat),
            "longitude": str(lng),
            "radius": radius_miles,
            "searchType": "GeoLocation",
            "excludePrivateFacilities": False,
            "rateType": "all",
            "currentClientDate": f"{date_ymd}T12:00:00.000Z",
            "facilityId": None,
            "facilityIds": [],
        }

        logger.info("[GolfNow] calling tee-times-with-inventory...")
        result = await page.evaluate(
            """
            async (payload) => {
                try {
                    const r = await fetch('/api/tee-times/tee-times-with-inventory', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                        },
                        body: JSON.stringify(payload)
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch (e) {
                    return { status: 0, error: e.message };
                }
            }
            """,
            payload,
        )

        status = result.get("status")
        if status == 200:
            try:
                data = json.loads(result["body"])
                captured.append({
                    "url": "/api/tee-times/tee-times-with-inventory",
                    "data": data,
                })
                total     = data.get("total", 0)
                n_slots   = sum(
                    len(f.get("teeTimes", []))
                    for f in data.get("ttResults", [])
                    if isinstance(f, dict)
                )
                logger.info(
                    f"[GolfNow] inventory: {total} facilities, {n_slots} tee time slots"
                )
                if debug:
                    fname = f"debug_inventory_{search_date}.json"
                    with open(fname, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
                    logger.debug(f"[GolfNow] saved inventory response -> {fname}")
            except Exception as exc:
                logger.warning(f"[GolfNow] failed to parse inventory response: {exc}")
        else:
            logger.warning(
                f"[GolfNow] tee-times-with-inventory returned status {status}"
            )
            if debug:
                logger.debug(
                    f"[GolfNow] response preview: {result.get('body', '')[:300]}"
                )

        if debug:
            html  = await page.content()
            fname = f"debug_golfnow_{search_date}.html"
            with open(fname, "w", encoding="utf-8") as fh:
                fh.write(html)
            logger.debug(f"[GolfNow] saved page HTML -> {fname}")
            logger.debug(f"[GolfNow] total captured responses: {len(captured)}")

        await ctx.close()
        await browser.close()

    return captured


# ── Parsing ────────────────────────────────────────────────────────────────────

def _parse_all(captured: list, user_lat: float, user_lng: float, group_size: int) -> list:
    results = []
    for item in captured:
        results.extend(_try_parse(item["data"], item["url"], user_lat, user_lng, group_size))
    return results


def _try_parse(data, url: str, user_lat: float, user_lng: float, group_size: int) -> list:
    if not isinstance(data, dict):
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                if _has_tee_times_key(first):
                    return _parse_facility_list(data, user_lat, user_lng, group_size)
                return _parse_flat_list(data, user_lat, user_lng, group_size)
        return []

    # tee-times-with-inventory format: {"ttResults": [...], "total": N}
    tt_results = data.get("ttResults")
    if isinstance(tt_results, list) and tt_results:
        first = tt_results[0]
        if isinstance(first, dict) and "teeTimes" in first:
            return _parse_inventory_list(tt_results, user_lat, user_lng, group_size)

    # courses-near-me format: {"ttResults": {"facilities": [...]}}
    if isinstance(tt_results, dict):
        facs = tt_results.get("facilities", [])
        if facs:
            return _parse_facility_list(facs, user_lat, user_lng, group_size)

    # Generic key-based search (legacy / unknown formats)
    for key in ("Facilities", "facilities", "Courses", "courses",
                "Results", "results", "data", "items", "teeTimes", "TeeTimes"):
        if key in data and isinstance(data[key], list) and data[key]:
            return _parse_facility_list(data[key], user_lat, user_lng, group_size)

    if _has_tee_times_key(data):
        return _parse_facility(data, user_lat, user_lng, group_size)

    return []


# ── Inventory format (tee-times-with-inventory) ────────────────────────────────

def _parse_inventory_list(facilities: list, user_lat, user_lng, group_size) -> list:
    result = []
    for fac in facilities:
        result.extend(_parse_inventory_facility(fac, user_lat, user_lng, group_size))
    return result


def _parse_inventory_facility(fac: dict, user_lat: float, user_lng: float, group_size: int) -> list:
    course_id   = str(fac.get("facilityId", "0"))
    course_name = fac.get("name", "Unknown Course")

    addr_obj = fac.get("address", {}) or {}
    addr = ", ".join(filter(None, [
        addr_obj.get("line1", ""),
        addr_obj.get("city", ""),
        addr_obj.get("stateProvinceCode", ""),
    ]))

    c_lat  = float(fac.get("latitude") or user_lat)
    c_lng  = float(fac.get("longitude") or user_lng)
    c_type = "Private" if fac.get("isPrivate") else "Public"
    rating = fac.get("averageRating")
    dist   = float(fac.get("distance") or 0) or _haversine_miles(user_lat, user_lng, c_lat, c_lng)

    result = []
    for slot in fac.get("teeTimes", []):
        tt = _parse_inventory_slot(
            slot, course_id, course_name, addr, c_lat, c_lng,
            c_type, rating, dist, group_size,
        )
        if tt:
            result.append(tt)
    return result


def _parse_inventory_slot(
    slot: dict,
    course_id: str,
    course_name: str,
    course_address: str,
    c_lat: float,
    c_lng: float,
    c_type: str,
    rating,
    dist: float,
    group_size: int,
) -> Optional[TeeTime]:
    date_obj = slot.get("date") or {}
    time_raw = date_obj.get("date") if isinstance(date_obj, dict) else None
    if not time_raw:
        return None
    tee_dt = _parse_dt(str(time_raw))
    if tee_dt is None:
        return None

    display       = slot.get("displayRate") or {}
    holes         = int(display.get("holeCount") or 18)
    cart_included = bool(display.get("isCartIncluded", False))

    price_obj  = slot.get("price") or {}
    price_val  = float(price_obj.get("value") or 0.0)

    max_players = _parse_max_players(slot.get("players", "OneTwoThreeFour"))
    tee_time_id = str(slot.get("teeTimeId", "0"))
    detail_url  = slot.get("detailUrl", "")
    booking_url = (
        f"https://www.golfnow.com{detail_url}"
        if detail_url else "https://www.golfnow.com"
    )

    return TeeTime(
        id=f"{course_id}_{tee_dt.strftime('%Y%m%d_%H%M')}_{holes}_{tee_time_id}",
        course_name=course_name,
        course_address=course_address,
        course_lat=c_lat,
        course_lng=c_lng,
        course_type=c_type,
        course_rating=float(rating) if rating is not None else None,
        course_slope=None,
        distance_miles=round(dist, 1),
        tee_datetime=tee_dt,
        holes=holes,
        max_players=max_players,
        available_spots=max_players,
        cart_included=cart_included,
        price_per_player=round(price_val, 2),
        price_raw=price_val,
        price_is_per_group=False,
        booking_url=booking_url,
    )


def _parse_max_players(players_str: str) -> int:
    """Parse player rule like 'OneTwoThree' -> 3."""
    if "Four"  in players_str: return 4
    if "Three" in players_str: return 3
    if "Two"   in players_str: return 2
    if "One"   in players_str: return 1
    return 4


# ── Legacy facility/slot format (kept for forward compatibility) ───────────────

def _has_tee_times_key(obj: dict) -> bool:
    return any(k in obj for k in
               ("TeeTimes", "teeTimes", "tee_times", "TeeSheets", "teeSheets", "slots"))


def _parse_facility_list(facilities: list, user_lat, user_lng, group_size) -> list:
    result = []
    for f in facilities:
        result.extend(_parse_facility(f, user_lat, user_lng, group_size))
    return result


def _parse_facility(f: dict, user_lat: float, user_lng: float, group_size: int) -> list:
    course_id   = str(_get(f, "Id", "id", "FacilityId", "facilityId", "courseId", default="0"))
    course_name = _get(f, "Name", "name", "FacilityName", "facilityName", "CourseName", "courseName", default="Unknown Course")
    addr = ", ".join(filter(None, [
        _get(f, "Address", "address", "Address1", default=""),
        _get(f, "City", "city", default=""),
        _get(f, "State", "state", "StateProvince", default=""),
    ]))
    c_lat  = float(_get(f, "Latitude", "latitude", "Lat", "lat", default=user_lat))
    c_lng  = float(_get(f, "Longitude", "longitude", "Lng", "lng", default=user_lng))
    c_type = _get(f, "Type", "type", "CourseType", "courseType", "FacilityType", default="Public")
    rating = _get(f, "Rating", "rating", "StarRating", "starRating", "GoogleRating")
    slope  = _get(f, "Slope", "slope", "SlopeRating", "slopeRating")
    dist   = _haversine_miles(user_lat, user_lng, c_lat, c_lng)

    tee_times_raw = _get(f, "TeeTimes", "teeTimes", "tee_times",
                         "TeeSheets", "teeSheets", "slots", default=[])
    result = []
    for raw in tee_times_raw:
        tt = _parse_slot(raw, course_id, course_name, addr, c_lat, c_lng,
                         c_type, rating, slope, dist, group_size)
        if tt:
            result.append(tt)
    return result


def _parse_flat_list(items: list, user_lat: float, user_lng: float, group_size: int) -> list:
    result = []
    for raw in items:
        course_id   = str(_get(raw, "FacilityId", "facilityId", "CourseId", "courseId", default="0"))
        course_name = _get(raw, "FacilityName", "facilityName", "CourseName", "courseName", default="Unknown")
        addr = ", ".join(filter(None, [
            _get(raw, "Address", "address", default=""),
            _get(raw, "City", "city", default=""),
            _get(raw, "State", "state", default=""),
        ]))
        c_lat  = float(_get(raw, "Latitude", "latitude", default=user_lat))
        c_lng  = float(_get(raw, "Longitude", "longitude", default=user_lng))
        c_type = _get(raw, "Type", "type", "CourseType", default="Public")
        rating = _get(raw, "Rating", "rating")
        slope  = _get(raw, "Slope", "slope")
        dist   = _haversine_miles(user_lat, user_lng, c_lat, c_lng)
        tt = _parse_slot(raw, course_id, course_name, addr, c_lat, c_lng,
                         c_type, rating, slope, dist, group_size)
        if tt:
            result.append(tt)
    return result


def _parse_slot(raw, course_id, course_name, course_address,
                c_lat, c_lng, c_type, rating, slope, dist, group_size) -> Optional[TeeTime]:
    time_raw = _get(raw, "StartTime", "startTime", "TeeTime", "teeTime",
                    "DateTime", "dateTime", "Date", "date", "time", "SlotTime", "slotTime")
    if not time_raw:
        return None
    tee_dt = _parse_dt(str(time_raw))
    if tee_dt is None:
        return None

    holes        = int(_get(raw, "Holes", "holes", "NumberOfHoles", "numberOfHoles", default=18))
    max_players  = int(_get(raw, "MaxPlayers", "maxPlayers", "Players", "players",
                            "MaxGolfers", "maxGolfers", "Capacity", "capacity", default=4))
    avail_spots  = int(_get(raw, "AvailablePlayers", "availablePlayers",
                            "SpotsAvailable", "spotsAvailable", "Available", "available",
                            default=max_players))

    cart_raw = _get(raw, "CartIncluded", "cartIncluded", "IncludesCart", "includesCart",
                    "Cart", "cart", "CartRental", "cartRental", default=False)
    cart_included = (cart_raw.lower() in ("true", "yes", "1", "included")
                     if isinstance(cart_raw, str) else bool(cart_raw))

    price_raw            = float(_get(raw, "Price", "price", "Rate", "rate",
                                       "GreenFee", "greenFee", "TotalPrice", "totalPrice",
                                       "Amount", "amount", default=0.0) or 0.0)
    price_per_player_fld = _get(raw, "PricePerPlayer", "pricePerPlayer", "PlayerRate", "playerRate")
    price_per_group_fld  = _get(raw, "PricePerGroup", "pricePerGroup", "GroupRate", "groupRate")
    cart_fee             = float(_get(raw, "CartFee", "cartFee", "CartPrice", "cartPrice", default=0.0) or 0.0)

    if price_per_player_fld is not None:
        price_per_player = float(price_per_player_fld)
        is_per_group = False
    elif price_per_group_fld is not None:
        price_per_player = float(price_per_group_fld) / max(group_size, 1)
        is_per_group = True
    elif price_raw > 250:
        price_per_player = price_raw / max(group_size, 1)
        is_per_group = True
    else:
        price_per_player = price_raw
        is_per_group = False

    if not cart_included and cart_fee > 0:
        price_per_player += cart_fee
        cart_included = True

    booking_url = _get(raw, "BookingUrl", "bookingUrl", "Url", "url",
                       "Link", "link", "HRef", "href", default="https://www.golfnow.com")

    return TeeTime(
        id=f"{course_id}_{tee_dt.strftime('%Y%m%d_%H%M')}_{holes}",
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
        available_spots=avail_spots,
        cart_included=cart_included,
        price_per_player=round(price_per_player, 2),
        price_raw=price_raw,
        price_is_per_group=is_per_group,
        booking_url=str(booking_url),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(obj: dict, *keys, default=None):
    for k in keys:
        if k in obj:
            return obj[k]
    return default


_DT_FMTS = [
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M %p",
]

def _parse_dt(s: str) -> Optional[datetime]:
    # Strip timezone offset (+HH:MM, -HH:MM, or trailing Z) before parsing.
    # GolfNow stores local tee times tagged as +00:00, so we take the local part.
    s = s.split("+")[0].rstrip("Z").strip()
    for fmt in _DT_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _haversine_miles(lat1, lng1, lat2, lng2) -> float:
    R = 3_958.8
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
