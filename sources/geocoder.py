import json
import requests
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / ".geocache.json"


def geocode(address: str) -> tuple:
    """Return (lat, lng) for an address using OpenStreetMap Nominatim (free, no key)."""
    cache = _load_cache()
    if address in cache:
        return cache[address]["lat"], cache[address]["lng"]

    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "GolfCoursePriceChecker/1.0"},
        timeout=10,
    )
    resp.raise_for_status()

    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode: {address!r}")

    lat = float(results[0]["lat"])
    lng = float(results[0]["lon"])
    cache[address] = {"lat": lat, "lng": lng}
    _save_cache(cache)
    return lat, lng


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict):
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
