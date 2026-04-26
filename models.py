from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TeeTime:
    # Unique identifier (course_id + date + time + holes) for dedup
    id: str

    # Course
    course_name: str
    course_address: str
    course_lat: float
    course_lng: float
    course_type: str            # "Public", "Semi-Private", "Private"
    course_rating: Optional[float]
    course_slope: Optional[int]
    distance_miles: float

    # Tee time slot
    tee_datetime: datetime
    holes: int                  # 9 or 18
    max_players: int
    available_spots: int
    cart_included: bool

    # Price
    price_per_player: float     # always per player, with cart factored in
    price_raw: float            # as published (may be per group)
    price_is_per_group: bool    # True if price_raw was a group rate

    # Booking
    booking_url: str

    # Weather (populated by filters.py)
    temp_f: Optional[float] = None
    rain_chance: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_dir: Optional[str] = None
