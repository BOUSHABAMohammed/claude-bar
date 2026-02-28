#!/usr/bin/env python3
"""Claude Usage macOS Menu Bar App

Displays real usage data from claude.ai, refreshing every 5 minutes.
Run: python claude_bar.py
"""

import datetime
from datetime import timezone

import rumps
import rookiepy
from curl_cffi import requests

REFRESH_INTERVAL = 300  # seconds
COOKIE_NAMES = ("sessionKey", "__Secure-next-auth.session-token")


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

def fmt_reset(iso: str) -> str:
    """Format time remaining until reset as '2h 44m'."""
    dt = datetime.datetime.fromisoformat(iso)
    now = datetime.datetime.now(timezone.utc)
    remaining = dt - now
    total_secs = max(0, int(remaining.total_seconds()))
    h, rem = divmod(total_secs, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


def fmt_date(iso: str) -> str:
    """Format reset date as 'Fri Mar 06'."""
    return datetime.datetime.fromisoformat(iso).strftime("%a %b %d")


def bar(pct: float, width: int = 20) -> str:
    """Return a Unicode progress bar like ▓▓▓▓░░░░░░░░░░░░░░░░"""
    filled = max(0, min(width, round(pct / 100 * width)))
    return "▓" * filled + "░" * (width - filled)


def warn_icon(pct: float) -> str:
    if pct >= 80:
        return "🔴"
    if pct >= 60:
        return "🟡"
    return "⚡"


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

        self.refresh_btn  = rumps.MenuItem("  ⟳ Refresh Now", callback=self.on_refresh)
        self.last_item    = rumps.MenuItem("  Last updated: —")

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
            self.refresh_btn,
            self.last_item,
            None,
        ]

        self._session: requests.Session | None = None
        self._org_id: str | None = None

        self._timer = rumps.Timer(self._refresh, REFRESH_INTERVAL)
        self._timer.start()
        self._refresh(None)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_refresh(self, _):
        self._refresh(None)

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

        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            if code == 401:
                self._session = None
                self._org_id = None
                self.title = "⚡ 🔑"
                self.last_item.title = "  Error: session expired — refresh to retry"
            else:
                self.title = f"⚡ err {code}"
                self.last_item.title = f"  HTTP error {code}"

        except Exception as e:
            self.title = "⚡ ?"
            self.last_item.title = f"  Error: {e}"
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

        # 5-hour block
        self.five_h_row.title = (
            f"  {fh_pct:.0f}%  {bar(fh_pct)}  resets in {fmt_reset(fh['resets_at'])}"
        )

        # 7-day block
        self.seven_d_row.title = (
            f"  {sd_pct:.0f}%  {bar(sd_pct)}  resets {fmt_date(sd['resets_at'])}"
        )

        # Extra credits
        if ex.get("is_enabled"):
            used  = ex.get("used_credits", 0)
            limit = ex.get("monthly_limit", 0)
            util  = ex.get("utilization", 0)
            self.credits_row.title = (
                f"  ${used:.2f} used of ${limit:,.0f}  ({util:.2f}%)"
            )
        else:
            self.credits_row.title = "  not enabled"

        # Title bar
        max_pct = max(fh_pct, sd_pct)
        self.title = f"{warn_icon(max_pct)} 5h:{fh_pct:.0f}%  7d:{sd_pct:.0f}%"
        self.last_item.title = (
            f"  Last updated {datetime.datetime.now():%H:%M:%S}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ClaudeBar().run()
