"""NSAttributedString helpers for claude_bar.py — Claude Code CLI dark theme."""

from enum import Enum

from AppKit import (
    NSMutableAttributedString, NSAttributedString,
    NSColor, NSFont,
    NSForegroundColorAttributeName, NSFontAttributeName,
)


class ColorKey(str, Enum):
    """Named color keys for the menu bar palette."""
    HEADER = "header"
    BAR_FILLED = "bar_filled"
    BAR_EMPTY = "bar_empty"
    PCT_GREEN = "pct_green"
    PCT_AMBER = "pct_amber"
    PCT_RED = "pct_red"
    RESET_TIME = "reset_time"
    CREDITS = "credits"
    LAST_UPDATED = "last_updated"
    ERROR = "error"


PROGRESS_WIDTH = 20

# ── Palette ──────────────────────────────────────────────────────────────────
_HEX: dict[ColorKey, str] = {
    ColorKey.HEADER:       "#E0E0E0",
    ColorKey.BAR_FILLED:   "#4EC9B0",
    ColorKey.BAR_EMPTY:    "#404040",
    ColorKey.PCT_GREEN:    "#6A9955",
    ColorKey.PCT_AMBER:    "#FFCC02",
    ColorKey.PCT_RED:      "#F44747",
    ColorKey.RESET_TIME:   "#808080",
    ColorKey.CREDITS:      "#CE9178",
    ColorKey.LAST_UPDATED: "#555555",
    ColorKey.ERROR:        "#F44747",
}


def _hex_to_nscolor(hex_color: str):
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


_COLORS = {key: _hex_to_nscolor(hex_val) for key, hex_val in _HEX.items()}
_FONT_REG = NSFont.menuFontOfSize_(13.0)
_FONT_BOLD = NSFont.boldSystemFontOfSize_(13.0)
_FONT_MONO = NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _attrs(color_key: ColorKey, bold: bool = False, mono: bool = False) -> dict:
    return {
        NSForegroundColorAttributeName: _COLORS[color_key],
        NSFontAttributeName: _FONT_BOLD if bold else (_FONT_MONO if mono else _FONT_REG),
    }


def _attr_str(text: str, color_key: ColorKey, bold: bool = False, mono: bool = False):
    return NSAttributedString.alloc().initWithString_attributes_(text, _attrs(color_key, bold, mono))


def _progress_bar(pct: float, width: int = PROGRESS_WIDTH) -> tuple[str, str]:
    filled = min(round(pct / 100 * width), width)
    return "▓" * filled, "░" * (width - filled)


def make_plain(text: str, color_key: ColorKey, bold: bool = False):
    """Single-color NSAttributedString."""
    return _attr_str(text, color_key, bold=bold)


def set_menu_title(menuitem, attr_string) -> None:
    """Apply attributed string to a rumps.MenuItem. Never assign .title afterwards."""
    menuitem._menuitem.setAttributedTitle_(attr_string)


def pct_color_key(pct: float) -> ColorKey:
    return ColorKey.PCT_RED if pct >= 80 else ColorKey.PCT_AMBER if pct >= 60 else ColorKey.PCT_GREEN


def make_section_header(label: str):
    """◆ (teal) + bold white label."""
    result = NSMutableAttributedString.alloc().init()
    result.appendAttributedString_(_attr_str("◆ ", ColorKey.BAR_FILLED))
    result.appendAttributedString_(_attr_str(label, ColorKey.HEADER, bold=True))
    return result


def make_progress_row(pct: float, suffix: str, width: int = PROGRESS_WIDTH):
    """Colored progress row: percentage + ▓▓▓░░░ + suffix."""
    filled, empty = _progress_bar(pct, width)
    result = NSMutableAttributedString.alloc().init()
    result.appendAttributedString_(_attr_str(f"  {pct:.0f}%  ", pct_color_key(pct)))
    result.appendAttributedString_(_attr_str(filled, ColorKey.BAR_FILLED, mono=True))
    result.appendAttributedString_(_attr_str(empty, ColorKey.BAR_EMPTY, mono=True))
    result.appendAttributedString_(_attr_str(suffix, ColorKey.RESET_TIME))
    return result
