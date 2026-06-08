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
SERVICE_NAME="${SERVICE_NAME:-goodwe-guru}"
SERVICE_USER="${SERVICE_USER:-goodwe}"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLU}[INFO]${NC}  $*"; }
ok()    { echo -e "${GRN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YLW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; exit 1; }

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
  npm ci --silent
  npm run build --silent
  cd "$APP_DIR"
else
  warn "--quick: skipping Python deps and frontend rebuild"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

info "Restarting $SERVICE_NAME …"
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  ok "Update complete — $SERVICE_NAME running at $AFTER"
else
  error "Service failed to start. Logs: journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi
