"""
Golf Course Price Checker
Usage:
  python main.py setup                               first-run wizard
  python main.py scan                                scan today, email + print
  python main.py scan --date 2026-05-10              scan a specific date
  python main.py scan --from 2026-05-10 --to 2026-05-17   date range (sorted by distance)
  python main.py scan --debug                        verbose output + saves GolfNow HTML
  python main.py run                                 start background scheduler (Ctrl+C to stop)
  python main.py task-scheduler                      create Windows Task Scheduler entry
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_LOG_FILE = Path(__file__).parent / "golf_checker.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_scan(args):
    from checker import load_config, run_check

    config = load_config(args.config)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.date_from and args.date_to:
        d_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(args.date_to, "%Y-%m-%d").date()
        dates = [d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)]
        print(f"Scanning {len(dates)} dates ({d_from} → {d_to})…")
        run_check(config, dates=dates, mode="range", debug=args.debug, print_results=True)

    elif args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        print(f"Scanning {target}…")
        run_check(config, dates=[target], mode="scan", debug=args.debug, print_results=True)

    else:
        print(f"Scanning today ({date.today()})…")
        run_check(config, dates=[date.today()], mode="scan", debug=args.debug, print_results=True)


def cmd_run(args):
    """Start the background scheduler. Uses BackgroundScheduler + a sleep loop
    so Ctrl+C works reliably on Windows."""
    import time
    from apscheduler.schedulers.background import BackgroundScheduler
    from checker import load_config, run_check

    config = load_config(args.config)
    hours = config["schedule"].get("check_interval_hours", 2)

    scheduler = BackgroundScheduler()

    def job():
        logging.info("Scheduled check starting…")
        run_check(config, mode="scheduled")

    scheduler.add_job(job, "interval", hours=hours, next_run_time=datetime.now())
    scheduler.start()
    print(f"Scheduler running — check every {hours}h. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        scheduler.shutdown(wait=False)
        print("Scheduler stopped.")


def cmd_setup(args):
    from setup_wizard import run_wizard
    run_wizard()


def cmd_task_scheduler(args):
    from scheduler_setup import setup_windows_task
    setup_windows_task()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Golf Course Price Checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="config.yaml", metavar="FILE")
    sub = parser.add_subparsers(dest="command")

    # scan
    sp = sub.add_parser("scan", help="Manual scan (email + terminal output)")
    sp.add_argument("--date", metavar="YYYY-MM-DD", help="Scan a specific date")
    sp.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD")
    sp.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD")
    sp.add_argument("--debug", action="store_true", help="Verbose + save GolfNow HTML")
    sp.set_defaults(func=cmd_scan)

    # run
    rp = sub.add_parser("run", help="Start background scheduler")
    rp.set_defaults(func=cmd_run)

    # setup
    wp = sub.add_parser("setup", help="First-run configuration wizard")
    wp.set_defaults(func=cmd_setup)

    # task-scheduler
    tp = sub.add_parser("task-scheduler", help="Create Windows Task Scheduler entry")
    tp.set_defaults(func=cmd_task_scheduler)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
