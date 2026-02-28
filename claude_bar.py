#!/usr/bin/env python3
"""Claude Usage macOS Menu Bar App

Displays real usage data from claude.ai, refreshing every 5 minutes.
Run: python claude_bar.py
"""

import json
import datetime
from datetime import timezone
from pathlib import Path

import rumps
import rookiepy
from curl_cffi import requests

STATS_PATH = Path.home() / ".claude" / "stats-cache.json"
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
    # Prefer "uuid", fall back to "id"
    return org.get("uuid") or org["id"]


def fetch_usage(session: requests.Session, org_id: str) -> dict:
    resp = session.get(
        f"https://claude.ai/api/organizations/{org_id}/usage",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Local token data
# ---------------------------------------------------------------------------

def get_today_tokens() -> dict:
    """Return {model_short_name: token_count} for today from stats-cache.json."""
    try:
        data = json.loads(STATS_PATH.read_text())
        today = datetime.date.today().isoformat()
        for entry in data.get("dailyModelTokens", []):
            if entry.get("date") == today:
                return entry.get("tokensByModel", {})
    except Exception:
        pass
    return {}


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
    """Return an ASCII progress bar like [████░░░░░░░░░░░░░░░░]."""
    filled = round(pct / 100 * width)
    filled = max(0, min(width, filled))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def warn_icon(pct: float) -> str:
    if pct >= 80:
        return "🔴"
    if pct >= 60:
        return "🟡"
    return "⚡"


def model_short(full_name: str) -> str:
    """'claude-opus-4-6' → 'Opus'"""
    parts = full_name.split("-")
    # parts[1] is the family name (opus/sonnet/haiku)
    if len(parts) >= 2:
        return parts[1].capitalize()
    return full_name


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeBar(rumps.App):
    def __init__(self):
        super().__init__("Claude", "⚡ …")

        # Static menu items
        self.five_h_bar   = rumps.MenuItem("  …")
        self.five_h_title = rumps.MenuItem("5h block:  loading…")
        self.five_h_reset = rumps.MenuItem("  resets in …")

        self.seven_d_bar   = rumps.MenuItem("  …")
        self.seven_d_title = rumps.MenuItem("7d block:  loading…")
        self.seven_d_reset = rumps.MenuItem("  resets …")

        self.extra_item  = rumps.MenuItem("Credits: loading…")
        self.tokens_sep  = rumps.MenuItem("── Tokens today ──────────────")
        self.last_item   = rumps.MenuItem("Last updated: —")
        self.refresh_btn = rumps.MenuItem("Refresh Now  ⟳", callback=self.on_refresh)

        self.menu = [
            self.five_h_title,
            self.five_h_bar,
            self.five_h_reset,
            None,
            self.seven_d_title,
            self.seven_d_bar,
            self.seven_d_reset,
            None,
            self.extra_item,
            None,
            self.tokens_sep,
            None,
            self.refresh_btn,
            self.last_item,
            None,
        ]

        self._session: requests.Session | None = None
        self._org_id: str | None = None
        self._token_items: dict[str, rumps.MenuItem] = {}

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

            data   = fetch_usage(self._session, self._org_id)
            tokens = get_today_tokens()
            self._update_menu(data, tokens)

        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            if code == 401:
                self._session = None
                self._org_id  = None
                self.title = "⚡ 🔑"
                self.last_item.title = "Error: session expired — refresh to retry"
            else:
                self.title = f"⚡ err {code}"
                self.last_item.title = f"HTTP error {code}"
        except Exception as e:
            self.title = "⚡ ?"
            self.last_item.title = f"Error: {e}"
            print(f"[claude_bar] refresh error: {e}")

    # ------------------------------------------------------------------
    # Menu update
    # ------------------------------------------------------------------

    def _update_menu(self, data: dict, tokens: dict):
        fh = data["five_hour"]
        sd = data["seven_day"]
        ex = data.get("extra_usage") or {}

        fh_pct = fh["utilization"]
        sd_pct = sd["utilization"]

        # 5-hour block
        self.five_h_title.title = f"5h block:  {fh_pct:.0f}%"
        self.five_h_bar.title   = f"  {bar(fh_pct)}"
        self.five_h_reset.title = f"  Resets in {fmt_reset(fh['resets_at'])}"

        # 7-day block
        self.seven_d_title.title = f"7d block:  {sd_pct:.0f}%"
        self.seven_d_bar.title   = f"  {bar(sd_pct)}"
        self.seven_d_reset.title = f"  Resets {fmt_date(sd['resets_at'])}"

        # Extra credits
        if ex.get("is_enabled"):
            used  = ex.get("used_credits", 0)
            limit = ex.get("monthly_limit", 0)
            util  = ex.get("utilization", 0)
            self.extra_item.title = (
                f"Credits: ${used:.2f} of ${limit:.0f}  ({util:.2f}%)"
            )
        else:
            self.extra_item.title = "Credits: not enabled"

        # Token items — insert/update below the separator
        for full_name, count in tokens.items():
            short = model_short(full_name)
            label = f"  {short:<10} {count:>9,}"
            if short not in self._token_items:
                item = rumps.MenuItem(label)
                # Insert before the blank separator that precedes Refresh
                self.menu.insert(self.menu.index(None, self.menu.index(self.tokens_sep) + 1), item)
                self._token_items[short] = item
            else:
                self._token_items[short].title = label

        # Title bar
        max_pct = max(fh_pct, sd_pct)
        self.title = f"{warn_icon(max_pct)} 5h:{fh_pct:.0f}%  7d:{sd_pct:.0f}%"
        self.last_item.title = (
            f"Last updated  {datetime.datetime.now():%H:%M:%S}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ClaudeBar().run()
