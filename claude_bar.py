#!/usr/bin/env python3
"""Claude Usage macOS Menu Bar App

Displays real usage data from claude.ai, refreshing every 5 minutes.
Run: python claude_bar.py
"""

import argparse
import datetime
import json
import pathlib
import subprocess
import threading
import urllib.request
from datetime import timezone

import rumps
import rookiepy
from curl_cffi import requests
from PyObjCTools.AppHelper import callAfter
from color_utils import (
    ColorKey,
    make_plain, make_section_header, make_progress_row,
    set_menu_title,
)

VERSION = "0.1.3"  # bump this with each release
GITHUB_REPO = "BOUSHABAMohammed/claude-bar"

REFRESH_INTERVAL = 300  # seconds
ICON_SETUP_DELAY_SECS = 0.5  # give the run loop time to start before loading the icon
UPDATE_CHECK_DELAY_SECS = 5.0  # wait for run loop to settle before hitting GitHub
COOKIE_NAMES = ("sessionKey", "__Secure-next-auth.session-token")
BROWSERS = ("chrome", "safari", "firefox", "brave", "edge")  # edge support on macOS is limited in rookiepy
ICON_PATH = pathlib.Path(__file__).parent / "icons8-claude-ai-96.png"

# Set to False to hide the percentage summary next to the menu bar icon.
# Can also be toggled at runtime via the menu.
SHOW_TITLE_SUMMARY = True


# ---------------------------------------------------------------------------
# Update check
# ---------------------------------------------------------------------------

def _parse_version(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def check_for_update() -> tuple[str, str] | None:
    """Return (tag_name, html_url) if a newer release exists on GitHub, else None."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": "claude-bar"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "")
        html_url = data.get("html_url", "")
        if tag and _parse_version(tag) > _parse_version(VERSION):
            return tag, html_url
    except Exception as exc:
        print(f"[claude_bar] update check error: {type(exc).__name__}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Auth / cookie helpers
# ---------------------------------------------------------------------------

def get_session_cookie(browser: str):
    """Return (name, value) for the first matching claude.ai session cookie."""
    try:
        loader = getattr(rookiepy, browser)
        for c in loader(["claude.ai"]):
            if c["name"] in COOKIE_NAMES:
                return c["name"], c["value"]
    except Exception as exc:
        print(f"[claude_bar] {browser}: {type(exc).__name__}")
    return None, None


def build_session(browser: str | None = None) -> requests.Session:
    """Build a requests.Session with the claude.ai session cookie attached."""
    browsers_to_try = (browser,) if browser else BROWSERS
    for b in browsers_to_try:
        name, val = get_session_cookie(b)
        if val:
            print(f"[claude_bar] Using cookie '{name}' from {b}")
            s = requests.Session(impersonate="chrome120")
            s.cookies.set(name, val, domain="claude.ai")
            return s
    if browser:
        raise RuntimeError(f"No session cookie found in '{browser}'.")
    raise RuntimeError(
        "No claude.ai session cookie found. "
        "Log in to Claude in Chrome or Safari first."
    )


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_org_id(session: requests.Session) -> str:
    resp = session.get("https://claude.ai/api/organizations", timeout=10)
    resp.raise_for_status()
    orgs = resp.json()
    if not orgs:
        raise RuntimeError("No organizations returned from API")
    org = orgs[0]
    org_id = org.get("uuid") or org.get("id")
    if not org_id:
        raise RuntimeError("Organization has no 'uuid' or 'id' field")
    return org_id


def fetch_usage(session: requests.Session, org_id: str) -> dict:
    resp = session.get(
        f"https://claude.ai/api/organizations/{org_id}/usage",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _parse_iso(iso) -> datetime.datetime | None:
    """Parse an ISO 8601 string to a timezone-aware datetime, or None if invalid."""
    if not isinstance(iso, str):
        return None
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def fmt_reset(iso) -> str:
    """Format time remaining until reset as '2h 44m'."""
    dt = _parse_iso(iso)
    if dt is None:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    total_secs = max(0, int((dt - datetime.datetime.now(timezone.utc)).total_seconds()))
    h, rem = divmod(total_secs, 3600)
    return f"{h}h {rem // 60:02d}m"


def fmt_date(iso) -> str:
    """Format reset date as 'Fri Mar 06 06:00' (Local Time)."""
    dt = _parse_iso(iso)
    if dt is None:
        return "unknown"
    local_dt = dt.astimezone()
    return local_dt.strftime("%a %b %d %H:%M %p")


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeBar(rumps.App):
    def __init__(self, browser: str | None = None):
        super().__init__("Claude", "⚡ …")
        self._browser = browser
        self._build_menu_items()
        self._init_state()
        self._update_toggle_label()
        set_menu_title(self.five_h_hdr, make_section_header("5-Hour Window"))
        set_menu_title(self.seven_d_hdr, make_section_header("7-Day Window"))
        set_menu_title(self.credits_hdr, make_section_header("Extra Credits"))
        self._set_credits_visible(False)  # hidden until first refresh confirms is_enabled
        self.update_item._menuitem.setHidden_(True)  # shown only when update is available
        self._refresh(None)
        rumps.Timer(self._setup_icon, ICON_SETUP_DELAY_SECS).start()
        rumps.Timer(self._start_update_check, UPDATE_CHECK_DELAY_SECS).start()

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------

    def _build_menu_items(self):
        self.five_h_hdr = rumps.MenuItem("◆ 5-Hour Window")
        self.five_h_row = rumps.MenuItem("  …")
        self.seven_d_hdr = rumps.MenuItem("◆ 7-Day Window")
        self.seven_d_row = rumps.MenuItem("  …")
        self.credits_hdr = rumps.MenuItem("◆ Extra Credits")
        self.credits_row = rumps.MenuItem("  …")
        self.refresh_btn = rumps.MenuItem("  ⟳ Refresh Now", callback=self.on_refresh)
        self.summary_toggle = rumps.MenuItem("", callback=self.on_toggle_summary)
        self.last_item = rumps.MenuItem("  Last updated: —")
        self.update_item = rumps.MenuItem("  🆕 Update available", callback=self.on_open_update)

        self.menu = [
            self.five_h_hdr, self.five_h_row, None,
            self.seven_d_hdr, self.seven_d_row, None,
            self.credits_hdr, self.credits_row, None,
            self.summary_toggle, self.refresh_btn, self.last_item, None,
            self.update_item,
        ]

    def _init_state(self):
        self._session: requests.Session | None = None
        self._org_id: str | None = None
        self._refreshing: bool = False
        self._refresh_lock = threading.Lock()
        self._show_summary: bool = SHOW_TITLE_SUMMARY
        self._icon_loaded: bool = False
        self._last_fh_pct: float | None = None
        self._last_sd_pct: float | None = None
        self._credits_shown: bool = False
        self._update_url: str = f"https://github.com/{GITHUB_REPO}/releases"

    # ------------------------------------------------------------------
    # Icon setup
    # ------------------------------------------------------------------

    def _setup_icon(self, _):
        if not ICON_PATH.exists():
            print(f"[claude_bar] icon not found: {ICON_PATH}")
            return
        self.icon = str(ICON_PATH)
        self.template = True
        self._icon_loaded = True
        self._apply_title()

    # ------------------------------------------------------------------
    # Title helpers
    # ------------------------------------------------------------------

    def _apply_title(self):
        """Set self.title based on current show_summary flag and last known data."""
        if self._show_summary and self._last_fh_pct is not None:
            self.title = f"{self._last_fh_pct:.0f}% · {self._last_sd_pct:.0f}%"
        elif self._icon_loaded:
            self.title = None  # icon-only
        # else: no data yet and icon not loaded — leave "⚡ …" placeholder untouched

    def _update_toggle_label(self):
        mark = "✓" if self._show_summary else "  "
        self.summary_toggle.title = f"  {mark} Show % in status bar"

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_refresh(self, _):
        self._refresh(None)

    def on_open_update(self, _):
        subprocess.Popen(["open", self._update_url])

    def on_toggle_summary(self, _):
        self._show_summary = not self._show_summary
        self._update_toggle_label()
        self._apply_title()

    @rumps.timer(REFRESH_INTERVAL)
    def _auto_refresh(self, _):
        self._refresh(None)

    # ------------------------------------------------------------------
    # Update check
    # ------------------------------------------------------------------

    def _start_update_check(self, timer):
        timer.stop()
        threading.Thread(target=self._check_update_bg, daemon=True).start()

    def _check_update_bg(self):
        result = check_for_update()
        if result:
            tag, url = result
            self._update_url = url
            callAfter(self._show_update_banner, tag)

    def _show_update_banner(self, tag: str):
        self.update_item.title = f"  🆕 Update available: {tag} — click to open"
        self.update_item._menuitem.setHidden_(False)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _ensure_session(self):
        if self._session is None:
            self._session = build_session(self._browser)
        if self._org_id is None:
            self._org_id = get_org_id(self._session)

    def _handle_error(self, exc: Exception):
        resp = getattr(exc, "response", None)
        if resp is not None:
            code = resp.status_code
            if code == 401:
                self._session = None
                self._org_id = None
                self.title = "⚡ 🔑"
                set_menu_title(self.last_item,
                    make_plain("  Error: session expired — refresh to retry", ColorKey.ERROR))
            else:
                self.title = f"⚡ err {code}"
                set_menu_title(self.last_item,
                    make_plain(f"  HTTP error {code}", ColorKey.ERROR))
        else:
            self.title = "⚡ ?"
            set_menu_title(self.last_item,
                make_plain(f"  Error: {type(exc).__name__}", ColorKey.ERROR))
            print(f"[claude_bar] refresh error: {type(exc).__name__}: {str(exc)[:200]}")

    def _refresh(self, _):
        with self._refresh_lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh_bg, daemon=True).start()

    def _refresh_bg(self):
        try:
            self._ensure_session()
            data = fetch_usage(self._session, self._org_id)
            callAfter(self._update_menu, data)
        except Exception as exc:
            callAfter(self._handle_error, exc)
        finally:
            with self._refresh_lock:
                self._refreshing = False

    # ------------------------------------------------------------------
    # Menu update
    # ------------------------------------------------------------------

    def _set_credits_visible(self, visible: bool):
        self.credits_hdr._menuitem.setHidden_(not visible)
        self.credits_row._menuitem.setHidden_(not visible)
        self._credits_shown = visible

    def _render_window(self, row_item, pct: float, suffix: str):
        set_menu_title(row_item, make_progress_row(pct, suffix))

    def _render_credits(self, extra_usage: dict):
        if extra_usage.get("is_enabled"):
            used = (extra_usage.get("used_credits") or 0) / 100
            limit = (extra_usage.get("monthly_limit") or 0) / 100
            util = extra_usage.get("utilization") or 0
            set_menu_title(self.credits_row,
                make_plain(f"  ${used:.2f} used of ${limit:,.0f}  ({util:.2f}%)", ColorKey.CREDITS))
            if not self._credits_shown:
                self._set_credits_visible(True)
        elif self._credits_shown:
            self._set_credits_visible(False)

    def _update_menu(self, data: dict):
        try:
            five_hour = data.get("five_hour")
            seven_day = data.get("seven_day")

            if five_hour is None or seven_day is None:
                print(f"[claude_bar] unexpected API response keys: {list(data.keys())}")
                set_menu_title(self.last_item,
                    make_plain("  Error: unexpected API response shape", ColorKey.ERROR))
                return

            fh_util = five_hour.get("utilization", 0.0)
            fh_resets = five_hour.get("resets_at")
            sd_util = seven_day.get("utilization", 0.0)
            sd_resets = seven_day.get("resets_at")

            self._render_window(self.five_h_row, fh_util,
                                "   Starts when a message is sent" if fmt_reset(fh_resets) == "unknown" else f"  resets in {fmt_reset(fh_resets)}")
            self._render_window(self.seven_d_row, sd_util,
                                f"  resets {fmt_date(sd_resets)}")

            # Update title and timestamp FIRST — before credits which may fail
            self._last_fh_pct = fh_util
            self._last_sd_pct = sd_util
            self._apply_title()
            set_menu_title(self.last_item,
                make_plain(f"  Last updated {datetime.datetime.now():%H:%M:%S}", ColorKey.LAST_UPDATED))

            # Credits section isolated — crash here won't affect title/timestamp
            try:
                self._render_credits(data.get("extra_usage") or {})
            except Exception as exc:
                print(f"[claude_bar] credits render error: {type(exc).__name__}: {exc}")

        except Exception as exc:
            print(f"[claude_bar] _update_menu error: {type(exc).__name__}: {exc}")
            set_menu_title(self.last_item,
                make_plain(f"  Error: {type(exc).__name__}", ColorKey.ERROR))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Claude usage menu bar app")
    parser.add_argument(
        "--browser",
        choices=list(BROWSERS),
        metavar="BROWSER",
        help=f"Browser to read session cookie from. Choices: {', '.join(BROWSERS)}",
    )
    args = parser.parse_args()
    ClaudeBar(browser=args.browser).run()


if __name__ == "__main__":
    main()
