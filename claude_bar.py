#!/usr/bin/env python3
"""Claude Usage macOS Menu Bar App

Displays real usage data from claude.ai, refreshing every 5 minutes.
Run: python claude_bar.py
"""

import argparse
import datetime
import pathlib
import threading
from datetime import timezone

import rumps
import rookiepy
from curl_cffi import requests
from color_utils import (
    make_plain, make_section_header, make_progress_row,
    set_menu_title,
)

REFRESH_INTERVAL = 300  # seconds
COOKIE_NAMES = ("sessionKey", "__Secure-next-auth.session-token")
BROWSERS = ("chrome", "safari", "firefox", "brave", "edge")  # edge support on macOS is limited in rookiepy
PROGRESS_WIDTH = 20
ICON_PATH = pathlib.Path(__file__).parent / "icons8-claude-ai-96.png"

# Set to False to hide the percentage summary next to the menu bar icon.
# Can also be toggled at runtime via the menu.
SHOW_TITLE_SUMMARY = True


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

def fmt_reset(iso) -> str:
    """Format time remaining until reset as '2h 44m'."""
    if not isinstance(iso, str):
        return "unknown"
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    total_secs = max(0, int((dt - datetime.datetime.now(timezone.utc)).total_seconds()))
    h, rem = divmod(total_secs, 3600)
    return f"{h}h {rem // 60:02d}m"


def fmt_date(iso) -> str:
    """Format reset date as 'Fri Mar 06'."""
    if not isinstance(iso, str):
        return "unknown"
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%a %b %d")


def _progress_bar(pct: float, width: int = PROGRESS_WIDTH) -> tuple[str, str]:
    filled = min(round(pct / 100 * width), width)
    return "▓" * filled, "░" * (width - filled)


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeBar(rumps.App):
    def __init__(self, browser: str | None = None):
        super().__init__("Claude", "⚡ …")
        self._browser = browser

        self.five_h_hdr   = rumps.MenuItem("◆ 5-Hour Window")
        self.five_h_row   = rumps.MenuItem("  …")
        self.seven_d_hdr  = rumps.MenuItem("◆ 7-Day Window")
        self.seven_d_row  = rumps.MenuItem("  …")
        self.credits_hdr  = rumps.MenuItem("◆ Extra Credits")
        self.credits_row  = rumps.MenuItem("  …")
        self.refresh_btn    = rumps.MenuItem("  ⟳ Refresh Now", callback=self.on_refresh)
        self.summary_toggle = rumps.MenuItem("", callback=self.on_toggle_summary)
        self.last_item      = rumps.MenuItem("  Last updated: —")

        self.menu = [
            self.five_h_hdr, self.five_h_row, None,
            self.seven_d_hdr, self.seven_d_row, None,
            self.credits_hdr, self.credits_row, None,
            self.summary_toggle, self.refresh_btn, self.last_item, None,
        ]

        self._session: requests.Session | None = None
        self._org_id: str | None = None
        self._refreshing: bool = False
        self._refresh_lock = threading.Lock()
        self._show_summary: bool = SHOW_TITLE_SUMMARY
        self._icon_loaded: bool = False
        self._last_fh_pct: float | None = None
        self._last_sd_pct: float | None = None
        self._credits_shown: bool = False

        self._update_toggle_label()
        set_menu_title(self.five_h_hdr,  make_section_header("5-Hour Window"))
        set_menu_title(self.seven_d_hdr, make_section_header("7-Day Window"))
        set_menu_title(self.credits_hdr, make_section_header("Extra Credits"))
        self._set_credits_visible(False)  # hidden until first refresh confirms is_enabled

        self._refresh(None)
        rumps.Timer(self._setup_icon, 0.5).start()

    # ------------------------------------------------------------------
    # Icon setup
    # ------------------------------------------------------------------

    def _setup_icon(self, _):
        if not ICON_PATH.exists():
            print(f"[claude_bar] icon not found: {ICON_PATH}")
            return
        self.icon         = str(ICON_PATH)
        self.template     = True
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

    def on_toggle_summary(self, _):
        self._show_summary = not self._show_summary
        self._update_toggle_label()
        self._apply_title()

    @rumps.timer(REFRESH_INTERVAL)
    def _auto_refresh(self, _):
        self._refresh(None)

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
                    make_plain("  Error: session expired — refresh to retry", "error"))
            else:
                self.title = f"⚡ err {code}"
                set_menu_title(self.last_item,
                    make_plain(f"  HTTP error {code}", "error"))
        else:
            self.title = "⚡ ?"
            set_menu_title(self.last_item,
                make_plain(f"  Error: {type(exc).__name__}", "error"))
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
            self._update_menu(data)
        except Exception as exc:
            self._handle_error(exc)
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
        filled, empty = _progress_bar(pct)
        set_menu_title(row_item, make_progress_row(pct, filled, empty, suffix))

    def _render_credits(self, ex: dict):
        if ex.get("is_enabled"):
            used  = ex.get("used_credits", 0)
            limit = ex.get("monthly_limit", 0)
            util  = ex.get("utilization", 0)
            set_menu_title(self.credits_row,
                make_plain(f"  ${used:.2f} used of ${limit:,.0f}  ({util:.2f}%)", "credits"))
            if not self._credits_shown:
                self._set_credits_visible(True)
        elif self._credits_shown:
            self._set_credits_visible(False)

    def _update_menu(self, data: dict):
        fh = data.get("five_hour")
        sd = data.get("seven_day")

        if fh is None or sd is None:
            print(f"[claude_bar] unexpected API response keys: {list(data.keys())}")
            set_menu_title(self.last_item,
                make_plain("  Error: unexpected API response shape", "error"))
            return

        fh_util   = fh.get("utilization", 0.0)
        fh_resets = fh.get("resets_at")
        sd_util   = sd.get("utilization", 0.0)
        sd_resets = sd.get("resets_at")

        self._render_window(self.five_h_row, fh_util,
                            f"  resets in {fmt_reset(fh_resets)}")
        self._render_window(self.seven_d_row, sd_util,
                            f"  resets {fmt_date(sd_resets)}")
        self._render_credits(data.get("extra_usage") or {})

        self._last_fh_pct = fh_util
        self._last_sd_pct = sd_util
        self._apply_title()

        set_menu_title(self.last_item,
            make_plain(f"  Last updated {datetime.datetime.now():%H:%M:%S}", "last_updated"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude usage menu bar app")
    parser.add_argument(
        "--browser",
        choices=list(BROWSERS),
        metavar="BROWSER",
        help=f"Browser to read session cookie from. Choices: {', '.join(BROWSERS)}",
    )
    args = parser.parse_args()
    ClaudeBar(browser=args.browser).run()
