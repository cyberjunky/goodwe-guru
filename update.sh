#!/usr/bin/env bash
# =============================================================================
# GoodWe Guru — in-container updater
#
# Pulls the latest code, rebuilds, and restarts the service. No reinstall,
# no touching apt/nginx/ufw/fail2ban.
#
# Run INSIDE the LXC as root:
#   goodwe-guru-update                 # full update (backend + frontend)
#   goodwe-guru-update --quick         # pull + restart only (no rebuilds)
#   goodwe-guru-update <git-ref>       # update to a branch/tag/commit
#   goodwe-guru-update <ref> --quick
#
# From the Proxmox HOST (replace 102 with your CTID):
#   pct exec 102 -- goodwe-guru-update
# =============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/goodwe-guru}"
DATA_DIR="${DATA_DIR:-/data/goodwe-guru}"
SERVICE_NAME="${SERVICE_NAME:-goodwe-guru}"
SERVICE_USER="${SERVICE_USER:-goodwe}"

TRIGGER="$DATA_DIR/.update-request"
STATUS="$DATA_DIR/.update-status.json"
LOG="$DATA_DIR/update.log"

# Running as root (systemd unit) against a goodwe-owned repo triggers git's
# "dubious ownership" guard. The systemd service has no HOME/gitconfig, so set
# both here — otherwise every GUI-triggered update fails at `git pull`.
export HOME="${HOME:-/root}"
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

# ── Status file (read by the dashboard's Update button) ────────────────────────
_status() { # state [message]
  [[ -d "$DATA_DIR" ]] || return 0
  local commit; commit="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo '')"
  printf '{"state":"%s","commit":"%s","ts":%s,"message":"%s"}\n' \
    "$1" "$commit" "$(date +%s)" "${2:-}" > "$STATUS" 2>/dev/null || true
}

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLU}[INFO]${NC}  $*"; }
ok()    { echo -e "${GRN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YLW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; _status failed "$*"; exit 1; }

# Consume the GUI trigger (if any) and mirror output to the update log
rm -f "$TRIGGER" 2>/dev/null || true
[[ -d "$DATA_DIR" ]] && exec > >(tee -a "$LOG") 2>&1
trap '_status failed "unexpected error — see update.log"' ERR

# ── Parse args (a --quick flag plus an optional git ref, any order) ────────────
QUICK=false
REF=""
for a in "$@"; do
  case "$a" in
    --quick|-q) QUICK=true ;;
    -*)         error "Unknown option: $a" ;;
    *)          REF="$a" ;;
  esac
done

[[ $EUID -ne 0 ]] && error "Run as root (inside the container)."
[[ -d "$APP_DIR/.git" ]] || error "$APP_DIR is not a git checkout — run install.sh first."

cd "$APP_DIR"
BEFORE=$(git rev-parse --short HEAD)
_status running

info "Fetching latest code …"
git fetch --all --prune --quiet
if [[ -n "$REF" ]]; then
  git checkout "$REF"
  git pull --ff-only origin "$REF" 2>/dev/null || true
else
  git pull --ff-only
fi
AFTER=$(git rev-parse --short HEAD)

if [[ "$BEFORE" == "$AFTER" ]]; then
  info "No new commits ($AFTER) — rebuilding/restarting anyway."
else
  info "Code: $BEFORE → $AFTER"
fi

if ! $QUICK; then
  # ── Python deps ─────────────────────────────────────────────────────────────
  [[ -x "$APP_DIR/.venv/bin/pip" ]] || { info "Creating venv …"; python3 -m venv "$APP_DIR/.venv"; }
  info "Updating Python dependencies …"
  "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
  "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

  # ── Frontend rebuild ──────────────────────────────────────────────────────────
  info "Rebuilding frontend …"
  cd "$APP_DIR/frontend"
  # No committed package-lock.json → npm ci would fail; fall back to npm install.
  if [[ -f package-lock.json ]]; then npm ci --silent; else npm install --silent; fi
  npm run build
  cd "$APP_DIR"
else
  warn "--quick: skipping Python deps and frontend rebuild"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# Ping (device detection) needs a raw ICMP socket. AmbientCapabilities alone
# proved unreliable on this host, so the service unit now runs with
# NoNewPrivileges=no + a setcap'd ping binary — same mechanism as a normal
# root/user shell, which is what we confirmed actually works. Ensure both
# halves are in place on every update, so this can't silently regress again.
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN="$DROPIN_DIR/override.conf"
mkdir -p "$DROPIN_DIR"
if [[ ! -f "$DROPIN" ]] || ! grep -q "NoNewPrivileges=no" "$DROPIN" 2>/dev/null; then
  printf '[Service]\nNoNewPrivileges=no\n' > "$DROPIN"
  systemctl daemon-reload
  ok "systemd override applied: NoNewPrivileges=no (needed for ping)"
fi
PING_BIN=$(command -v ping 2>/dev/null || true)
# NOTE: must not abort the script (set -e) if setcap fails for any reason —
# that would skip the restart below and leave the OLD sandbox config running,
# silently, with no restart ever happening. That exact bug is why this note
# exists: the fix looked applied (systemctl show reflected the new unit) but
# the live process was never restarted to pick it up.
if [[ -n "$PING_BIN" ]]; then
  if setcap cap_net_raw+ep "$PING_BIN" 2>/dev/null; then
    ok "cap_net_raw set on ping"
  else
    warn "setcap on ping failed — device ping detection may not work; continuing anyway"
  fi
fi

info "Restarting $SERVICE_NAME …"
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  _status ok
  ok "Update complete — $SERVICE_NAME running at $AFTER"
else
  error "Service failed to start. Logs: journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi
