#!/usr/bin/env python3
"""Claude Usage macOS Menu Bar App

Displays real usage data from claude.ai, refreshing every 5 minutes.
Run: python claude_bar.py
"""

import datetime
import pathlib
from datetime import timezone

import rumps
import rookiepy
from curl_cffi import requests
from color_utils import (
    make_plain, make_section_header, make_progress_row,
    set_menu_title, pct_color_key,
)

REFRESH_INTERVAL = 300  # seconds
COOKIE_NAMES = ("sessionKey", "__Secure-next-auth.session-token")

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
    except Exception:
        pass
    return None, None


def build_session() -> requests.Session:
    """Build a requests.Session with the claude.ai session cookie attached."""
    for browser in ("chrome", "safari", "firefox", "brave", "edge"):
        name, val = get_session_cookie(browser)
        if val:
            print(f"[claude_bar] Using cookie '{name}' from {browser}")
            s = requests.Session(impersonate="chrome120")
            s.cookies.set(name, val, domain="claude.ai")
            return s
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
    return org.get("uuid") or org["id"]


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
    dt = datetime.datetime.fromisoformat(iso)
    now = datetime.datetime.now(timezone.utc)
    remaining = dt - now
    total_secs = max(0, int(remaining.total_seconds()))
    h, rem = divmod(total_secs, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


def fmt_date(iso) -> str:
    """Format reset date as 'Fri Mar 06'."""
    if not isinstance(iso, str):
        return "unknown"
    return datetime.datetime.fromisoformat(iso).strftime("%a %b %d")


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeBar(rumps.App):
    def __init__(self):
        super().__init__("Claude", "⚡ …")

        # Section headers (act as disabled labels)
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
            self.five_h_hdr,
            self.five_h_row,
            None,
            self.seven_d_hdr,
            self.seven_d_row,
            None,
            self.credits_hdr,
            self.credits_row,
            None,
            self.summary_toggle,
            self.refresh_btn,
            self.last_item,
            None,
        ]

        self._session: requests.Session | None = None
        self._org_id: str | None = None
        self._show_summary: bool = SHOW_TITLE_SUMMARY
        self._icon_loaded: bool = False
        self._last_fh_pct: float | None = None
        self._last_sd_pct: float | None = None
        self._credits_shown: bool = True
        self._update_toggle_label()

        # Apply colored headers immediately (static, no data needed)
        set_menu_title(self.five_h_hdr,  make_section_header("5-Hour Window"))
        set_menu_title(self.seven_d_hdr, make_section_header("7-Day Window"))
        set_menu_title(self.credits_hdr, make_section_header("Extra Credits"))

        self._timer = rumps.Timer(self._refresh, REFRESH_INTERVAL)
        self._timer.start()
        self._refresh(None)

        # Schedule icon download after run() starts
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
        # else: leave loading text untouched

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

    def _refresh(self, _):
        try:
            if self._session is None:
                self._session = build_session()
            if self._org_id is None:
                self._org_id = get_org_id(self._session)

            data = fetch_usage(self._session, self._org_id)
            self._update_menu(data)

        except Exception as e:
            resp = getattr(e, "response", None)
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
                    make_plain(f"  Error: {e}", "error"))
                print(f"[claude_bar] refresh error: {e}")

    # ------------------------------------------------------------------
    # Menu update
    # ------------------------------------------------------------------

    def _update_menu(self, data: dict):
        fh = data["five_hour"]
        sd = data["seven_day"]
        ex = data.get("extra_usage") or {}

        fh_pct = fh["utilization"]
        sd_pct = sd["utilization"]

        w = 20
        fh_f = round(fh_pct / 100 * w)
        sd_f = round(sd_pct / 100 * w)

        # 5-hour block
        set_menu_title(self.five_h_row, make_progress_row(
            fh_pct, "▓" * fh_f, "░" * (w - fh_f),
            f"  resets in {fmt_reset(fh['resets_at'])}"))

        # 7-day block
        set_menu_title(self.seven_d_row, make_progress_row(
            sd_pct, "▓" * sd_f, "░" * (w - sd_f),
            f"  resets {fmt_date(sd['resets_at'])}"))

        # Extra credits — only shown when is_enabled
        if ex.get("is_enabled"):
            used  = ex.get("used_credits", 0)
            limit = ex.get("monthly_limit", 0)
            util  = ex.get("utilization", 0)
            set_menu_title(self.credits_row,
                make_plain(f"  ${used:.2f} used of ${limit:,.0f}  ({util:.2f}%)", "credits"))
            if not self._credits_shown:
                self.credits_hdr._menuitem.setHidden_(False)
                self.credits_row._menuitem.setHidden_(False)
                self._credits_shown = True
        else:
            if self._credits_shown:
                self.credits_hdr._menuitem.setHidden_(True)
                self.credits_row._menuitem.setHidden_(True)
                self._credits_shown = False

        # Title bar
        self._last_fh_pct = fh_pct
        self._last_sd_pct = sd_pct
        self._apply_title()

        set_menu_title(self.last_item,
            make_plain(f"  Last updated {datetime.datetime.now():%H:%M:%S}", "last_updated"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ClaudeBar().run()
