#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  claude-bar installer
# ============================================================
GITHUB_OWNER="BOUSHABAMohammed"
GITHUB_REPO="claude-bar"
# ============================================================

VERSION="${1:-latest}"
INSTALL_DIR="$HOME/.local/share/claude-bar"
LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/com.user.claude-bar.plist"

# --- Terminal colours ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

step() { echo -e "\n${GREEN}==>${NC} ${BOLD}$*${NC}"; }
warn() { echo -e "${YELLOW}Warning:${NC} $*"; }
die()  { echo -e "\n${RED}Error:${NC} $*" >&2; exit 1; }

# ============================================================
# 1. Resolve version
# ============================================================
if [[ "$VERSION" == "latest" ]]; then
    step "Resolving latest release..."
    VERSION=$(
        curl -fsSL \
            "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest" \
        2>/dev/null \
        | grep '"tag_name"' \
        | sed -E 's/.*"([^"]+)".*/\1/'
    ) || true
    [[ -z "$VERSION" ]] && die \
        "No releases found for ${GITHUB_OWNER}/${GITHUB_REPO}.\n\n" \
        "Create one first:\n" \
        "   git tag v0.1.0 && git push origin v0.1.0\n" \
        "Then publish the release at: https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/new"
    echo "   Latest version: ${VERSION}"
fi

# ============================================================
# 2. curl is guaranteed on macOS — sanity-check only
# ============================================================
command -v curl &>/dev/null || die "curl not found. This should never happen on macOS."

# ============================================================
# 3. Ensure uv is available
# ============================================================
if ! command -v uv &>/dev/null; then
    step "Installing uv (Python package manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add the default uv install location to PATH for the rest of this script
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null \
        || die "uv installation failed. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
else
    echo "   uv already installed: $(uv --version)"
fi

# ============================================================
# 4. Download release tarball
# ============================================================
step "Installing claude-bar ${VERSION} to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

TARBALL_URL="https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/archive/refs/tags/${VERSION}.tar.gz"
TMP_TARBALL=$(mktemp /tmp/claude-bar-XXXXXX.tar.gz)
trap 'rm -f "$TMP_TARBALL"' EXIT

echo "   Downloading ${TARBALL_URL}..."
curl -fsSL "$TARBALL_URL" -o "$TMP_TARBALL" \
    || die "Download failed. Verify that version ${VERSION} exists at:\n   https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases"

# ============================================================
# 5. Extract (strip GitHub's REPO-VERSION/ prefix)
# ============================================================
echo "   Extracting..."
tar -xzf "$TMP_TARBALL" -C "$INSTALL_DIR" --strip-components=1

# ============================================================
# 6. Strip Gatekeeper quarantine (prevents "unidentified developer" block)
# ============================================================
xattr -r -d com.apple.quarantine "$INSTALL_DIR" 2>/dev/null || true

# ============================================================
# 7. Install Python dependencies
#    uv will download Python 3.12 automatically if needed.
# ============================================================
step "Installing Python dependencies..."
(cd "$INSTALL_DIR" && uv sync --frozen --no-dev)

# ============================================================
# 8. Write run.sh into the install directory
# ============================================================
step "Writing run.sh..."
cat > "$INSTALL_DIR/run.sh" << 'RUNEOF'
#!/usr/bin/env bash
# Launcher for claude-bar — do not edit INSTALL_DIR directly,
# re-run the installer to update.
cd "$HOME/.local/share/claude-bar"
exec .venv/bin/python claude_bar.py "$@"
RUNEOF
chmod +x "$INSTALL_DIR/run.sh"

# ============================================================
# 9. Optional: install LaunchAgent (start at login)
# ============================================================
echo ""
read -r -p "Start claude-bar automatically at login? [y/N] " AUTOSTART
if [[ "${AUTOSTART,,}" == "y" ]]; then
    step "Installing LaunchAgent..."
    mkdir -p "$HOME/Library/LaunchAgents"

    cat > "$LAUNCHAGENT_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.claude-bar</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${INSTALL_DIR}/run.sh</string>
    </array>

    <!-- Start immediately when launchd loads the agent -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart only on crash; a normal quit (Cmd-Q) stays quit -->
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>

    <!-- Required for AppKit / menu bar apps under launchd -->
    <key>ProcessType</key>
    <string>Interactive</string>

    <!-- launchd does not inherit your shell PATH or HOME -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/claude-bar.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/claude-bar.log</string>
</dict>
</plist>
EOF

    launchctl load "$LAUNCHAGENT_PLIST"
    echo "   LaunchAgent installed and loaded — claude-bar will start at login."
fi

# ============================================================
# 10. Done
# ============================================================
echo ""
echo -e "${GREEN}${BOLD}✓ claude-bar ${VERSION} installed successfully!${NC}"
echo ""
echo "Run it now:"
echo "   $INSTALL_DIR/run.sh"
echo ""
echo "To uninstall:"
echo "   launchctl unload ~/Library/LaunchAgents/com.user.claude-bar.plist 2>/dev/null || true"
echo "   rm -rf ~/.local/share/claude-bar ~/Library/LaunchAgents/com.user.claude-bar.plist"
echo ""
