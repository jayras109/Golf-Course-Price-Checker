"""
Creates a Windows Task Scheduler entry that runs:
  python main.py scan
every 2 hours from 6 AM to 10 PM, every day.

The scan command runs once, emails any new qualifying tee times, then exits.
Task Scheduler handles the timing — no background process needed.
"""

import subprocess
import sys
from pathlib import Path


def setup_windows_task():
    python = sys.executable
    script = str(Path(__file__).parent / "main.py")
    task_name = "GolfCoursePriceChecker"

    print(f"\nSetting up Windows Task Scheduler…")
    print(f"  Python : {python}")
    print(f"  Script : {script}")
    print(f"  Task   : {task_name}")

    # schtasks /create flags:
    #   /sc HOURLY /mo 2  → every 2 hours
    #   /st 06:00 /et 22:00 /k → run between 6 AM and 10 PM; kill if running at end
    #   /f → overwrite if task already exists
    cmd = [
        "schtasks", "/create", "/f",
        "/tn", task_name,
        "/tr", f'"{python}" "{script}" scan',
        "/sc", "HOURLY",
        "/mo", "2",
        "/st", "06:00",
        "/et", "22:00",
        "/k",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"\n  Task '{task_name}' created — runs every 2 hours (6 AM – 10 PM).")
        print(f"\n  Manage it:")
        print(f"    View   : schtasks /query /tn {task_name} /fo LIST /v")
        print(f"    Run now: schtasks /run /tn {task_name}")
        print(f"    Delete : schtasks /delete /tn {task_name} /f")
    else:
        print(f"\n  schtasks failed: {result.stderr.strip()}")
        print("\n  Manual alternative — open Task Scheduler (taskschd.msc) and create:")
        print(f"    Action  : Start a program")
        print(f"    Program : {python}")
        print(f"    Args    : \"{script}\" scan")
        print(f"    Trigger : Daily, repeat every 2 hours, between 6 AM and 10 PM")
