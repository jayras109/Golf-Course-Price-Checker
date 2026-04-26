"""
Interactive first-run setup.
Writes config.yaml, sends a test email, and installs the Playwright browser.
"""

import os
import smtplib
import ssl
import subprocess
import sys
from email.mime.text import MIMEText
from pathlib import Path

import yaml


def run_wizard():
    print("\n=== Golf Course Price Checker — First-Time Setup ===\n")

    cfg = {}

    # Address
    print("1. Search address (courses will be found near this location)")
    cfg["address"] = input("   Address: ").strip()

    # Distance
    print("\n2. Maximum distance from that address")
    cfg["max_distance_miles"] = int(input("   Max miles (e.g. 25): ").strip())

    # Group size
    print("\n3. How many players in your group? (used to split group-rate prices)")
    cfg["group_size"] = int(input("   Players (e.g. 2): ").strip())

    # Prices
    print("\n4. Price thresholds — per player, cart included")
    p18 = input("   18-hole max $/player (leave blank to skip): ").strip()
    p9 = input("    9-hole max $/player (leave blank to skip): ").strip()
    cfg["price_thresholds"] = {
        "holes_18": float(p18) if p18 else None,
        "holes_9":  float(p9)  if p9  else None,
    }

    # Days
    print("\n5. Days to include in scheduled checks (default: fri sat sun)")
    print("   Enter comma-separated day abbreviations, or press Enter for default:")
    days_raw = input("   Days: ").strip()
    if days_raw:
        check_days = [d.strip().lower() for d in days_raw.split(",")]
    else:
        check_days = ["fri", "sat", "sun"]

    # Weather — defaults, no prompts (matches user spec)
    cfg["weather"] = {
        "max_rain_chance": 25,
        "min_temp_f": 45,
        "max_temp_f": 95,
    }

    # Schedule
    cfg["schedule"] = {
        "check_interval_hours": 2,
        "lookahead_days": 7,
        "check_days": check_days,
    }

    # Email
    print("\n6. Gmail setup")
    print("   You need a Gmail App Password (different from your Google password).")
    print("   Create one at: myaccount.google.com  → Security → App passwords")
    from_addr = input("   From Gmail address: ").strip()
    to_addr   = input("   Send alerts to (press Enter for same): ").strip() or from_addr
    app_pw    = input("   Gmail App Password: ").strip()

    cfg["email"] = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "from_address": from_addr,
        "to_address":   to_addr,
        "app_password": app_pw,
    }

    # Write config
    config_path = Path("config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"\n  Config saved → {config_path.absolute()}")

    # Test email
    print("\nSending test email…")
    try:
        msg = MIMEText(
            "<h2 style='font-family:sans-serif;color:#1a6e38'>Golf Checker is configured!</h2>"
            "<p style='font-family:sans-serif'>You will receive tee time alerts at this address.</p>",
            "html",
        )
        msg["Subject"] = "[Golf Checker] Setup successful"
        msg["From"] = from_addr
        msg["To"] = to_addr
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls(context=ctx)
            server.login(from_addr, app_pw)
            server.sendmail(from_addr, to_addr, msg.as_string())
        print("  Test email sent — check your inbox.")
    except Exception as exc:
        print(f"  Email test failed: {exc}")
        print("  Double-check your App Password and try again.")
        print("  (App Passwords require 2FA to be enabled on your Google account)")

    # Install Playwright browser
    print("\nInstalling Playwright Chromium browser (~150 MB, one-time download)…")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=False,
    )
    if result.returncode == 0:
        print("  Chromium installed.")
    else:
        print("  Chromium install may have failed. Run manually:")
        print("    python -m playwright install chromium")

    print("\n=== Setup complete ===")
    print("  python main.py scan                    — scan today")
    print("  python main.py scan --date 2026-05-10  — scan a specific date")
    print("  python main.py run                     — start background scheduler")
    print("  python main.py task-scheduler          — add Windows scheduled task")
