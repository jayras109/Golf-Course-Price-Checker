import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

from models import TeeTime


# ── Formatting helpers (cross-platform — avoids %-d / %-I which fail on Windows)

def _fmt_long(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%A, %B')} {dt.day} @ {hour}:{dt.strftime('%M')} {ampm}"


def _fmt_short(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a %b')} {dt.day} @ {hour}:{dt.strftime('%M')} {ampm}"


def _price_label(tt: TeeTime, group_size: int) -> str:
    if tt.price_is_per_group:
        group_total = tt.price_raw
        return f"${tt.price_per_player:.2f}/player (÷{group_size} from ${group_total:.2f} group rate)"
    return f"${tt.price_per_player:.2f}/player"


def _weather_row(tt: TeeTime) -> str:
    parts = []
    if tt.temp_f is not None:
        parts.append(f"{tt.temp_f:.0f}°F")
    if tt.rain_chance is not None:
        parts.append(f"{tt.rain_chance:.0f}% rain")
    if tt.wind_speed is not None:
        wind = f"{tt.wind_speed:.0f} mph"
        if tt.wind_dir:
            wind += f" {tt.wind_dir}"
        parts.append(wind)
    return " &nbsp;·&nbsp; ".join(parts) if parts else "N/A"


# ── HTML builder ───────────────────────────────────────────────────────────────

_ROW = """
<tr{bg}>
  <td style="padding:5px 12px 5px 0;color:#555;white-space:nowrap;width:130px;"><strong>{label}</strong></td>
  <td style="padding:5px 0;">{value}</td>
</tr>"""

_CARD = """
<div style="border:1px solid #d4d4d4;border-radius:8px;padding:18px;margin:14px 0;font-family:Arial,sans-serif;max-width:560px;">
  <h2 style="margin:0 0 2px;color:#1a6e38;font-size:18px;">{course_name}</h2>
  <p style="margin:0 0 14px;color:#777;font-size:13px;">{course_address} &nbsp;·&nbsp; {distance} mi away</p>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    {rows}
  </table>
  <div style="margin-top:14px;">
    <a href="{booking_url}"
       style="background:#1a6e38;color:#fff;padding:9px 20px;border-radius:5px;
              text-decoration:none;font-size:14px;font-weight:bold;display:inline-block;">
      Book on GolfNow
    </a>
  </div>
</div>"""


def _build_card(tt: TeeTime, group_size: int) -> str:
    rating_str = f"{tt.course_rating:.1f} ★" if tt.course_rating else "N/A"
    slope_str = str(tt.course_slope) if tt.course_slope else "N/A"

    rows_data = [
        ("Tee Time",    _fmt_long(tt.tee_datetime)),
        ("Price",       f'<span style="color:#1a6e38;font-weight:bold;">{_price_label(tt, group_size)}</span>'),
        ("Holes",       str(tt.holes)),
        ("Players",     f"Up to {tt.available_spots} (max {tt.max_players})"),
        ("Cart",        "Included ✓" if tt.cart_included else "Not included"),
        ("Course Type", tt.course_type or "N/A"),
        ("Rating",      rating_str),
        ("Slope",       slope_str),
        ("Weather",     _weather_row(tt)),
    ]

    rows_html = ""
    for i, (label, value) in enumerate(rows_data):
        bg = ' style="background:#f7f7f7;"' if i % 2 == 0 else ""
        rows_html += _ROW.format(bg=bg, label=label, value=value)

    return _CARD.format(
        course_name=tt.course_name,
        course_address=tt.course_address or "—",
        distance=tt.distance_miles,
        rows=rows_html,
        booking_url=tt.booking_url,
    )


def _build_html(tee_times: list, group_size: int, mode: str) -> str:
    sorted_tts = sorted(tee_times, key=lambda t: t.distance_miles)

    if mode == "range":
        header = f"Your date-range results — {len(tee_times)} qualifying tee times, sorted by distance"
    else:
        header = f"{len(tee_times)} new qualifying tee time{'s' if len(tee_times) != 1 else ''} found"

    cards = "".join(_build_card(tt, group_size) for tt in sorted_tts)

    return f"""<!DOCTYPE html>
<html><body style="max-width:600px;margin:0 auto;padding:16px;font-family:Arial,sans-serif;">
  <h1 style="color:#1a6e38;">&#9971; Golf Tee Time Alert</h1>
  <p style="color:#444;">{header}</p>
  {cards}
  <p style="font-size:11px;color:#aaa;margin-top:24px;">Golf Course Price Checker</p>
</body></html>"""


# ── Send ───────────────────────────────────────────────────────────────────────

def send_email(tee_times: list, config: dict, mode: str = "alert"):
    if not tee_times:
        return

    cfg = config["email"]
    group_size = config.get("group_size", 2)

    if mode == "range":
        first = min(tee_times, key=lambda t: t.tee_datetime)
        last = max(tee_times, key=lambda t: t.tee_datetime)
        subject = (
            f"[Golf] {len(tee_times)} tee times "
            f"({first.tee_datetime.strftime('%b')} {first.tee_datetime.day} – "
            f"{last.tee_datetime.strftime('%b')} {last.tee_datetime.day})"
        )
    else:
        best = min(tee_times, key=lambda t: t.price_per_player)
        subject = (
            f"[Golf Alert] {best.course_name} — "
            f"{_fmt_short(best.tee_datetime)} — "
            f"${best.price_per_player:.0f}/player"
        )

    html = _build_html(tee_times, group_size, mode)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_address"]
    msg["To"] = cfg["to_address"]
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.login(cfg["from_address"], cfg["app_password"])
        server.sendmail(cfg["from_address"], cfg["to_address"], msg.as_string())

    print(f"  Email sent: {subject}")
