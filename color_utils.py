"""NSAttributedString helpers for claude_bar.py — Claude Code CLI dark theme."""

from AppKit import (
    NSMutableAttributedString, NSAttributedString,
    NSColor, NSFont,
    NSForegroundColorAttributeName, NSFontAttributeName,
)

# ── Palette ──────────────────────────────────────────────────────────────────
_HEX = {
    "header":       "#E0E0E0",
    "bar_filled":   "#4EC9B0",
    "bar_empty":    "#404040",
    "pct_green":    "#6A9955",
    "pct_amber":    "#FFCC02",
    "pct_red":      "#F44747",
    "reset_time":   "#808080",
    "credits":      "#CE9178",
    "last_updated": "#555555",
    "error":        "#F44747",
}

def _hex_to_nscolor(h):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)

_COLORS   = {k: _hex_to_nscolor(v) for k, v in _HEX.items()}
_FONT_REG  = NSFont.menuFontOfSize_(13.0)
_FONT_BOLD = NSFont.boldSystemFontOfSize_(13.0)
_FONT_MONO = NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _attrs(color_key, bold=False, mono=False):
    return {
        NSForegroundColorAttributeName: _COLORS[color_key],
        NSFontAttributeName: _FONT_BOLD if bold else (_FONT_MONO if mono else _FONT_REG),
    }

def _chunk(text, color_key, bold=False, mono=False):
    return NSAttributedString.alloc().initWithString_attributes_(text, _attrs(color_key, bold, mono))

def make_plain(text, color_key, bold=False):
    """Single-color NSAttributedString."""
    return _chunk(text, color_key, bold=bold)

def set_menu_title(menuitem, attr_string):
    """Apply attributed string to a rumps.MenuItem. Never assign .title afterwards."""
    menuitem._menuitem.setAttributedTitle_(attr_string)

def pct_color_key(pct):
    return "pct_red" if pct >= 80 else "pct_amber" if pct >= 60 else "pct_green"

def make_section_header(label):
    """◆ (teal) + bold white label."""
    r = NSMutableAttributedString.alloc().init()
    r.appendAttributedString_(_chunk("◆ ", "bar_filled"))
    r.appendAttributedString_(_chunk(label, "header", bold=True))
    return r

def make_progress_row(pct, filled, empty, suffix):
    """Colored progress row: percentage + ▓▓▓░░░ + suffix."""
    r = NSMutableAttributedString.alloc().init()
    r.appendAttributedString_(_chunk(f"  {pct:.0f}%  ", pct_color_key(pct)))
    r.appendAttributedString_(_chunk(filled, "bar_filled", mono=True))
    r.appendAttributedString_(_chunk(empty,  "bar_empty",  mono=True))
    r.appendAttributedString_(_chunk(suffix, "reset_time"))
    return r
