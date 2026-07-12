#!/data/data/com.termux/files/usr/bin/bash
# ═══════════════════════════════════════════════════════════════
# Tablet bot runner — Fast Forward Bots
#   One command:  bash ~/Fast_Foreward_Bots/Bot_Code_DB/run_tablet.sh
#   Keeps bot alive after SSH disconnect, CPU awake with screen off.
# ═══════════════════════════════════════════════════════════════

set -e

BOT_DIR="$HOME/Fast_Foreward_Bots/Bot_Code_DB"
SSHD_PORT=8022
SCREEN_NAME="fastbot"

# ─── Colors ───────────────────────────────────────────────────
GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }
header(){ echo -e "\n${BOLD}══ $1 ══${NC}"; }

# ─── Step 1: Kill stale bot processes ─────────────────────────
header "Cleaning up"
pkill -f 'python.*main\.py' 2>/dev/null && warn "Killed stale bot process" || true

# ─── Step 2: Ensure dependencies ──────────────────────────────
header "Dependencies"
DEPS=("screen" "git")
MISSING=()
for pkg in "${DEPS[@]}"; do
    if ! command -v "$pkg" &>/dev/null; then
        MISSING+=("$pkg")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    info "Installing: ${MISSING[*]}"
    pkg install -y "${MISSING[@]}"
fi

# ─── Step 3: git pull latest code ─────────────────────────────
header "Updating code"
if [ -d "$BOT_DIR" ]; then
    cd "$BOT_DIR"
    git pull --rebase
    info "Code updated to $(git rev-parse --short HEAD)"
else
    err "Bot directory not found at $BOT_DIR"
    info "Cloning..."
    cd "$HOME"
    git clone https://github.com/ahmedfalah666/Fast_Foreward_Bots.git
    cd "$BOT_DIR"
fi

# ─── Step 4: Ensure .env exists ───────────────────────────────
header "Configuration"
if [ ! -f .env ]; then
    err ".env file missing! Create it with:"
    err "  cat > .env << 'EOF'"
    err "  BOT_TOKEN=your_token_here"
    err "  ADMIN_IDS=your_admin_id"
    err "  STORAGE_CHANNEL_ID=your_channel_id"
    err "  BOT_INSTANCE_NAME=ipad"
    err "  DATABASE_URL=postgresql://user:pass@host/db?sslmode=require"
    err "  EOF"
    exit 1
fi
source .env
info "BOT_INSTANCE_NAME=${BOT_INSTANCE_NAME:-UNSET}"

# ─── Step 5: SSH server setup ─────────────────────────────────
header "SSH server (port $SSHD_PORT)"
SSHD_CFG="$PREFIX/etc/ssh/sshd_config"

# Set port
if grep -q "^Port " "$SSHD_CFG" 2>/dev/null; then
    sed -i "s/^Port .*/Port $SSHD_PORT/" "$SSHD_CFG"
else
    echo "Port $SSHD_PORT" >> "$SSHD_CFG"
fi

# Allow password auth (convenient for first-time setup)
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' "$SSHD_CFG" 2>/dev/null
if ! grep -q "PasswordAuthentication" "$SSHD_CFG" 2>/dev/null; then
    echo "PasswordAuthentication yes" >> "$SSHD_CFG"
fi

# Ensure sshd user databases exist
if [ ! -f "$PREFIX/etc/ssh/ssh_host_rsa_key" ]; then
    ssh-keygen -A
fi

# Kill existing sshd and restart
pkill -x sshd 2>/dev/null || true
sshd
info "sshd running on port $SSHD_PORT"

# Show login hint
IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP 'inet \K[\d.]+' || echo "<get IP with: ip addr>")
echo -e "  ${BOLD}ssh u0_a223@$IP -p $SSHD_PORT${NC}"

# ─── Step 6: Wake lock (CPU stays awake with screen off) ─────
header "Wake lock"
if command -v termux-wake-lock &>/dev/null; then
    termux-wake-lock
    info "Wake lock acquired — CPU will stay awake"
else
    warn "termux-wake-lock not found. CPU may sleep when screen is off."
    warn "Install Termux:API from F-Droid + 'pkg install termux-api'"
fi

# ─── Step 7: Start bot in screen session ──────────────────────
header "Starting bot"
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true

screen -dmS "$SCREEN_NAME" bash -c "
    cd '$BOT_DIR'
    source .env 2>/dev/null
    exec python -u main.py
"

# Wait briefly, verify
sleep 2
if screen -S "$SCREEN_NAME" -Q select . &>/dev/null; then
    info "Bot running in screen session '${SCREEN_NAME}'"
    echo -e "  ${BOLD}Attach:${NC}   screen -r $SCREEN_NAME"
    echo -e "  ${BOLD}Detach:${NC}   Ctrl+A, D"
    echo -e "  ${BOLD}Status:${NC}   screen -ls"
    echo -e "  ${BOLD}Logs:${NC}     tail -f $BOT_DIR/../bot.log (if configured)"
else
    err "Screen session failed to start. Check manually:"
    err "  cd $BOT_DIR && python main.py"
fi

# ─── Step 8: Show SSH login command again ─────────────────────
header "Ready"
echo -e "  Bot PID:  $(pgrep -f 'python.*main\.py' | head -1)"
echo -e "  Screen:   ${SCREEN_NAME}"
echo -e "  SSH:      ${BOLD}ssh u0_a223@$IP -p $SSHD_PORT${NC}"
