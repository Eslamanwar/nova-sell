#!/usr/bin/env python3
"""
Dubizzle local login helper — run this on your Mac.

Opens your real Chrome with the "Eslam" profile so Imperva sees a genuine
browser. Log in to Dubizzle by hand, then close the window.

Usage:
    pip install playwright

    WORKER_POD=novasell-worker-xxxxx python dubizzle_login_mac.py
"""

import json
import os
import sys
import pathlib
from playwright.sync_api import sync_playwright

CHROME_BASE  = pathlib.Path.home() / "Library/Application Support/Google/Chrome"
CHROME_PATH  = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_NAME = os.environ.get("CHROME_PROFILE", "Eslam")   # display name of the Chrome profile
POD_DIR      = "/data/novasell/nova-act-profile"
POD          = os.environ.get("WORKER_POD", "<worker-pod>")


def find_chrome_profile(base: pathlib.Path, name: str) -> pathlib.Path:
    """Return the profile directory whose display name matches `name`."""
    for candidate in base.iterdir():
        prefs = candidate / "Preferences"
        if not prefs.exists():
            continue
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
            if data.get("profile", {}).get("name") == name:
                return candidate
        except Exception:
            continue
    return None


def main():
    if not pathlib.Path(CHROME_PATH).exists():
        print(f"ERROR: Chrome not found at:\n  {CHROME_PATH}")
        sys.exit(1)

    profile_dir = find_chrome_profile(CHROME_BASE, PROFILE_NAME)
    if profile_dir is None:
        print(f"ERROR: Chrome profile '{PROFILE_NAME}' not found under {CHROME_BASE}")
        print("Available profiles:")
        for candidate in CHROME_BASE.iterdir():
            prefs = candidate / "Preferences"
            if prefs.exists():
                try:
                    n = json.loads(prefs.read_text())["profile"]["name"]
                    print(f"  {candidate.name}  →  {n}")
                except Exception:
                    pass
        sys.exit(1)

    # Remove stale lock files so Chrome starts cleanly
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = profile_dir / name
        if lock.exists():
            lock.unlink()

    print(f"Using Chrome profile : '{PROFILE_NAME}'  ({profile_dir})")
    print()
    print("Chrome will open. Log in to Dubizzle normally.")
    print("When you are done, CLOSE THE BROWSER WINDOW and the profile will be saved.")
    print()
    print("NOTE: Make sure Chrome is fully closed before running this script.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=CHROME_PATH,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://dubai.dubizzle.com/")

        # Wait until the user closes the browser
        browser.wait_for_event("close", timeout=0)

    print()
    print("=" * 60)
    print("Profile saved. Copy it to the worker pod with:")
    print()
    print(f"  kubectl cp \"{profile_dir}\" {POD}:{POD_DIR}")
    print()
    print("Then verify on the pod:")
    print()
    print(f"  kubectl exec -it {POD} -- python -m project.dubizzle_login")
    print("=" * 60)


if __name__ == "__main__":
    main()
