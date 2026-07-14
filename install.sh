#!/usr/bin/env bash
# =============================================================================
# GoodWe Guru — Proxmox VE LXC Helper Script
#
# Run on the Proxmox HOST:
#   bash <(curl -fsSL https://raw.githubusercontent.com/cyberjunky/goodwe-guru/main/install.sh)
#
# Or run manually inside any Debian 13 / Ubuntu 24.04 system:
#   curl -fsSL https://raw.githubusercontent.com/cyberjunky/goodwe-guru/main/install.sh | bash
#
# What it does:
#   1. Creates a hardened Debian 13 (Trixie) LXC on Proxmox
#   2. Installs all dependencies (Python 3, Node.js 20, nginx, certbot, fail2ban)
#   3. Clones this repo, builds the React frontend, installs Python backend
#   4. Creates a sandboxed systemd service
#   5. Configures nginx with rate-limiting, security headers, HSTS
#   6. (Optional) Obtains a Let's Encrypt certificate
#   7. Configures fail2ban for nginx
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLU}[INFO]${NC}  $*"; }
ok()    { echo -e "${GRN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YLW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; exit 1; }

# ── Detect Proxmox host ───────────────────────────────────────────────────────
ON_PROXMOX=false
command -v pct &>/dev/null && ON_PROXMOX=true

# ── Static config ─────────────────────────────────────────────────────────────
APP_DIR="/opt/goodwe-guru"
DATA_DIR="/data/goodwe-guru"
SERVICE_USER="goodwe"
SERVICE_NAME="goodwe-guru"
NGINX_CONF="/etc/nginx/sites-available/goodwe-guru"
NODE_MAJOR=20
REPO_URL="${REPO_URL:-https://github.com/cyberjunky/goodwe-guru.git}"

# ── Interactive prompts ───────────────────────────────────────────────────────
# Skip the whole wizard when values are already supplied via the environment.
# This is the case for the in-container re-exec (`bash -s < "$0"`), where stdin
# IS the script — running `read` there would consume the script's own lines and
# desync execution (symptom: "info: command not found").
PASSWORD_GENERATED=false
if [[ -z "${INVERTER_HOST:-}" ]]; then
  echo ""
  echo -e "${YLW}╔════════════════════════════════════════════╗${NC}"
  echo -e "${YLW}║         GoodWe Guru — Setup Wizard         ║${NC}"
  echo -e "${YLW}╚════════════════════════════════════════════╝${NC}"
  echo ""

  read -rp "  GoodWe inverter IP address         : " INVERTER_HOST
  [[ -z "$INVERTER_HOST" ]] && error "Inverter IP is required"

  read -rp "  Dashboard password (blank = auto-gen): " -s APP_PASSWORD; echo
  if [[ -z "$APP_PASSWORD" ]]; then
    APP_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))" 2>/dev/null \
                   || openssl rand -base64 18 | tr -d '/+=')
    PASSWORD_GENERATED=true
    ok "Auto-generated dashboard password — shown in the summary below"
  else
    [[ ${#APP_PASSWORD} -lt 8 ]] && { warn "Password is short (< 8 chars) — consider a stronger one"; }
  fi

  read -rp "  Poll interval in seconds [20]       : " POLL_INTERVAL
  POLL_INTERVAL=${POLL_INTERVAL:-20}

  read -rp "  Domain for HTTPS (blank = IP-only)  : " DOMAIN
  if [[ -n "$DOMAIN" ]]; then
    read -rp "  Email for Let's Encrypt (blank=skip): " LE_EMAIL
  else
    read -rp "  Web port [80]                       : " HTTP_PORT
    HTTP_PORT=${HTTP_PORT:-80}
  fi

  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
               || openssl rand -hex 32)
fi

# =============================================================================
# ── PROXMOX: CREATE LXC ───────────────────────────────────────────────────────
# =============================================================================
if $ON_PROXMOX; then
  echo ""
  info "Proxmox host detected — creating LXC container …"

  VMID=$(pvesh get /cluster/nextid)

  # ── Container rootfs storage (block storage is fine here) ───────────────
  # Prefer local-zfs, then local-lvm, then local
  for store in local-zfs local-lvm local; do
    if pvesm status --content rootdir 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$store"; then
      STORAGE=$store; break
    fi
  done
  [[ -z "${STORAGE:-}" ]] && STORAGE=$(pvesm status --content rootdir | awk 'NR>1 {print $1; exit}')
  [[ -z "${STORAGE:-}" ]] && error "No storage available for the container rootfs"

  # ── Template storage (MUST support 'vztmpl' content — usually 'local') ──
  # LVM/ZFS block storages cannot hold templates; using one yields:
  #   lvm name 'vztmpl/debian-…tar.zst' contains illegal characters
  TEMPLATE_STORAGE=$(pvesm status --content vztmpl 2>/dev/null | awk 'NR>1 {print $1; exit}')
  [[ -z "${TEMPLATE_STORAGE:-}" ]] && error \
    "No storage supports container templates (vztmpl). Enable the 'Container template' content type on a directory storage such as 'local'."

  info "Downloading Debian 13 (Trixie) template to '$TEMPLATE_STORAGE' …"
  pveam update >/dev/null 2>&1 || true
  # Try Debian 13 first, fall back to 12
  TEMPLATE=$(pveam available --section system 2>/dev/null \
    | grep -E "debian-13|debian-trixie" | tail -1 | awk '{print $2}')
  if [[ -z "$TEMPLATE" ]]; then
    warn "Debian 13 template not found — falling back to Debian 12"
    TEMPLATE=$(pveam available --section system | grep "debian-12-standard" | tail -1 | awk '{print $2}')
  fi
  [[ -z "$TEMPLATE" ]] && error "No Debian template found. Run: pveam update"

  # Download only if not already present (do NOT swallow errors — they used to
  # stay hidden until the much more cryptic 'pct create' failure)
  if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi

  info "Creating hardened LXC $VMID …"
  # NOTE: 2 GB RAM + 2 cores — the Vite frontend build OOM-kills at 512 MB
  # (no error, just a silent SIGKILL). Runtime needs far less; lower it after
  # the first install with:  pct set $VMID --memory 768 --cores 1
  pct create "$VMID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname goodwe-guru \
    --cores 2 \
    --memory 2048 \
    --swap 1024 \
    --storage "$STORAGE" \
    --rootfs "$STORAGE:4" \
    --net0 "name=eth0,bridge=vmbr0,ip=dhcp,firewall=1" \
    --unprivileged 1 \
    --features "nesting=1" \
    --onboot 1 \
    --start 1 \
    --description "GoodWe Guru — solar dashboard"

  # Harden LXC: drop all Linux capabilities except what's needed
  # (no cap_net_admin, no cap_sys_admin, no cap_dac_override, etc.)
  cat >> "/etc/pve/lxc/${VMID}.conf" <<EOF
# Security hardening
lxc.cap.drop = sys_module mac_admin mac_override sys_time net_admin sys_rawio
lxc.apparmor.profile = unconfined
EOF

  sleep 8
  info "LXC $VMID started. Running install inside container …"

  # Run the installer INSIDE the container by cloning the repo there. This works
  # whether THIS script was launched from a file or piped via process
  # substitution (bash <(curl …)) — in the latter case $0 is a pipe we can't copy.
  # (Do NOT pipe the script via `bash -s < "$0"`: that makes stdin the script
  #  itself, so apt/npm/curl reading stdin desync execution.)
  pct exec "$VMID" -- env \
    INVERTER_HOST="$INVERTER_HOST" \
    APP_PASSWORD="$APP_PASSWORD" \
    POLL_INTERVAL="$POLL_INTERVAL" \
    JWT_SECRET="$JWT_SECRET" \
    DOMAIN="${DOMAIN:-}" \
    LE_EMAIL="${LE_EMAIL:-}" \
    HTTP_PORT="${HTTP_PORT:-80}" \
    NODE_MAJOR="$NODE_MAJOR" \
    APP_DIR="$APP_DIR" \
    DATA_DIR="$DATA_DIR" \
    SERVICE_USER="$SERVICE_USER" \
    SERVICE_NAME="$SERVICE_NAME" \
    NGINX_CONF="$NGINX_CONF" \
    REPO_URL="$REPO_URL" \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    bash -c '
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -qq git >/dev/null
      rm -rf /root/goodwe-guru-src
      git clone --depth 1 "$REPO_URL" /root/goodwe-guru-src
      exec bash /root/goodwe-guru-src/install.sh
    '

  LXC_IP=$(pct exec "$VMID" -- hostname -I 2>/dev/null | awk '{print $1}')
  echo ""
  echo -e "${GRN}╔════════════════════════════════════════════╗${NC}"
  echo -e "${GRN}║    GoodWe Guru installed successfully!     ║${NC}"
  echo -e "${GRN}╚════════════════════════════════════════════╝${NC}"
  echo ""
  PORT_SFX=""; [[ "${HTTP_PORT:-80}" != 80 ]] && PORT_SFX=":${HTTP_PORT}"
  echo -e "  ${BLU}Container ID :${NC} $VMID"
  echo -e "  ${BLU}Local URL    :${NC} http://${LXC_IP:-<container-ip>}${PORT_SFX}"
  [[ -n "${DOMAIN:-}" ]] && echo -e "  ${BLU}Secure URL   :${NC} https://$DOMAIN"
  echo -e "  ${BLU}Password     :${NC} ${APP_PASSWORD}"
  $PASSWORD_GENERATED && echo -e "               ${YLW}(auto-generated — save it now)${NC}"
  echo -e "  ${BLU}Logs         :${NC} pct exec $VMID -- journalctl -u $SERVICE_NAME -f"
  echo -e "  ${BLU}Update       :${NC} pct exec $VMID -- goodwe-guru-update"
  echo ""
  exit 0
fi

# =============================================================================
# ── IN-CONTAINER / DIRECT INSTALL ────────────────────────────────────────────
# =============================================================================
[[ $EUID -ne 0 ]] && error "Please run as root."

# ── System packages ───────────────────────────────────────────────────────────
info "Updating package lists …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  curl git ca-certificates gnupg \
  nginx certbot python3-certbot-nginx \
  python3 python3-pip python3-venv \
  build-essential openssl \
  fail2ban ufw \
  libcap2-bin                    # for setcap (port binding without root)

ok "Base packages installed"

# Device ping detection needs a raw ICMP socket. AmbientCapabilities alone
# (the "correct" systemd way) proved unreliable in practice, so the service
# unit runs with NoNewPrivileges=no + a setcap'd ping binary instead — the
# same mechanism a normal root/user shell uses, which is what's confirmed
# to actually work.
PING_BIN=$(command -v ping 2>/dev/null || true)
[[ -n "$PING_BIN" ]] && setcap cap_net_raw+ep "$PING_BIN" && ok "cap_net_raw granted to ping"

# ── Node.js ───────────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ "$(node --version | cut -d. -f1 | tr -d v)" -lt "$NODE_MAJOR" ]]; then
  info "Installing Node.js $NODE_MAJOR …"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - >/dev/null 2>&1
  apt-get install -y -qq nodejs
fi
ok "Node $(node --version)"

# ── Service user ──────────────────────────────────────────────────────────────
# No -m: creating $APP_DIR here (with /etc/skel files) makes the later
# `git clone` fail with "destination path already exists and is not empty".
id "$SERVICE_USER" &>/dev/null \
  || useradd -r -s /usr/sbin/nologin -M -d "$APP_DIR" "$SERVICE_USER"

# ── Data directory ────────────────────────────────────────────────────────────
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR"

# ── Write config ──────────────────────────────────────────────────────────────
cat > "$DATA_DIR/config.env" <<EOF
INVERTER_HOST=${INVERTER_HOST:-192.168.1.100}
APP_PASSWORD=${APP_PASSWORD:-changeme}
POLL_INTERVAL=${POLL_INTERVAL:-20}
JWT_SECRET=${JWT_SECRET}
JWT_EXPIRE_DAYS=30
DB_PATH=${DATA_DIR}/history.db
EOF
chmod 600 "$DATA_DIR/config.env"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/config.env"
ok "Config written to $DATA_DIR/config.env"

# ── Clone / update app ────────────────────────────────────────────────────────
if [[ -d "$APP_DIR/.git" ]]; then
  info "Updating existing install …"
  git -C "$APP_DIR" pull --ff-only
else
  # Clear any non-git leftovers (e.g. a stale skel-populated dir from a prior run)
  [[ -d "$APP_DIR" ]] && rm -rf "$APP_DIR"
  info "Cloning GoodWe Guru …"
  git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# ── Install the 'goodwe-guru-update' convenience command ───────────────────────
# Pull latest code + rebuild + restart, without a full reinstall:
#   goodwe-guru-update            (full)     goodwe-guru-update --quick   (fast)
if [[ -f "$APP_DIR/update.sh" ]]; then
  ln -sf "$APP_DIR/update.sh" /usr/local/bin/goodwe-guru-update
  chmod +x "$APP_DIR/update.sh"
  ok "Installed 'goodwe-guru-update' command"
fi

# ── Python venv ───────────────────────────────────────────────────────────────
info "Installing Python dependencies …"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
ok "Python venv ready"

# ── Build React frontend ──────────────────────────────────────────────────────
info "Building frontend … (this is the heaviest step — needs ≥1.5 GB RAM)"
cd "$APP_DIR/frontend"
# Repo has no committed package-lock.json (it's .gitignored), and `npm ci`
# requires one — fall back to `npm install`.
if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
npm run build
# A silent OOM-kill leaves no error but no dist either — fail loudly here.
[[ -f "$APP_DIR/frontend/dist/index.html" ]] \
  || error "Frontend build produced no dist/ — usually out of memory. Give the LXC more RAM: pct set <id> --memory 2048"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/frontend/dist"
ok "Frontend built"

# ── Systemd service (sandboxed) ───────────────────────────────────────────────
info "Creating sandboxed systemd service …"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=GoodWe Guru
Documentation=https://github.com/cyberjunky/goodwe-guru
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${DATA_DIR}/config.env

ExecStart=${APP_DIR}/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000

Restart=on-failure
RestartSec=10
TimeoutStopSec=30

# ── Systemd sandboxing ───────────────────────────────────────────
# (NoNewPrivileges is set further down, alongside the ping capability note)
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=no   # needed by Python
SystemCallArchitectures=native

# Allow read-write to app data only
ReadWritePaths=${DATA_DIR}
ReadOnlyPaths=${APP_DIR}

# CAP_NET_RAW: needed for ping (ICMP) used by device detection.
# NoNewPrivileges is intentionally OFF: with it on, a setcap'd ping binary
# loses its capability the moment this service execs it (secure-exec clears
# the parent's ambient set), so ping silently fails while working fine from
# an interactive shell. Off, ping's own file capability just works, same as
# for any normal user.
NoNewPrivileges=no
CapabilityBoundingSet=CAP_NET_RAW
AmbientCapabilities=CAP_NET_RAW

StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
ok "Systemd service enabled and started"

# ── GUI-triggered updates ──────────────────────────────────────────────────────
# The sandboxed dashboard can't update itself, so it drops a trigger file in
# DATA_DIR. This privileged path-unit notices the file and runs the updater
# (git pull + rebuild + restart). The trigger content is never executed.
info "Enabling dashboard 'Update' button …"
cat > "/etc/systemd/system/${SERVICE_NAME}-update.service" <<EOF
[Unit]
Description=GoodWe Guru — apply update (triggered from dashboard)

[Service]
Type=oneshot
Environment=HOME=/root APP_DIR=${APP_DIR} DATA_DIR=${DATA_DIR} SERVICE_NAME=${SERVICE_NAME} SERVICE_USER=${SERVICE_USER}
ExecStart=${APP_DIR}/update.sh
EOF

cat > "/etc/systemd/system/${SERVICE_NAME}-update.path" <<EOF
[Unit]
Description=Watch for GoodWe Guru update requests

[Path]
PathExists=${DATA_DIR}/.update-request
Unit=${SERVICE_NAME}-update.service

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}-update.path"
ok "Dashboard 'Update' button enabled"

# ── UFW firewall ──────────────────────────────────────────────────────────────
info "Configuring UFW firewall …"
HTTP_PORT="${HTTP_PORT:-80}"
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow ssh >/dev/null                       # keep SSH open
ufw allow "${HTTP_PORT}/tcp" >/dev/null        # dashboard HTTP
[[ -n "${DOMAIN:-}" ]] && ufw allow 443/tcp >/dev/null   # HTTPS when a domain is set
ufw --force enable >/dev/null
ok "UFW: SSH (22) and dashboard port ${HTTP_PORT}$([[ -n "${DOMAIN:-}" ]] && echo ' + 443') allowed inbound"

# ── nginx — rate-limited, hardened, with security headers ────────────────────
info "Configuring nginx …"
SERVER_NAME="${DOMAIN:-_}"
HTTP_PORT="${HTTP_PORT:-80}"

# Shared rate limit zone (defined at http level)
# NOTE: no `server_tokens` here — Debian's nginx.conf already sets it, and a
# second definition makes `nginx -t` fail with "directive is duplicate".
cat > /etc/nginx/conf.d/goodwe-limits.conf <<'EOF'
# Rate limiting — prevents brute-force against login endpoint
limit_req_zone  $binary_remote_addr zone=login:10m  rate=5r/m;
limit_req_zone  $binary_remote_addr zone=api:10m    rate=60r/m;
limit_req_zone  $binary_remote_addr zone=ws:10m     rate=10r/m;
limit_conn_zone $binary_remote_addr zone=addr:10m;
EOF

# Port 80 ALWAYS serves the app directly (so the internal IP works), and is the
# default_server for any host. When a domain + cert exist we ADD a 443 block
# below; we deliberately do NOT 301-redirect 80→443, so IP-only access keeps
# working on the LAN.
cat > "$NGINX_CONF" <<NGINX
server {
    listen ${HTTP_PORT} default_server;
    listen [::]:${HTTP_PORT} default_server;
    server_name ${SERVER_NAME};

    include /etc/nginx/snippets/goodwe-proxy.conf;
}
NGINX

# Shared proxy snippet
mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/goodwe-proxy.conf <<'SNIPPET'
    # Connection limit
    limit_conn addr 20;

    # Security headers
    add_header X-Frame-Options              "SAMEORIGIN"            always;
    add_header X-Content-Type-Options       "nosniff"               always;
    add_header X-XSS-Protection             "1; mode=block"         always;
    add_header Referrer-Policy              "strict-origin"         always;
    add_header Permissions-Policy           "geolocation=(), camera=()" always;
    add_header Content-Security-Policy
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss: ws: https://api.forecast.solar; img-src 'self' data:; font-src 'self'; frame-ancestors 'none';"
        always;

    # Login endpoint — strict rate limit
    location /api/auth/login {
        limit_req zone=login burst=3 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket endpoints
    location ~ ^/ws/ {
        limit_req zone=ws burst=5 nodelay;
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade          $http_upgrade;
        proxy_set_header   Connection       "upgrade";
        proxy_set_header   Host             $host;
        proxy_set_header   X-Real-IP        $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # API endpoints
    location /api/ {
        limit_req zone=api burst=30 nodelay;
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host             $host;
        proxy_set_header   X-Real-IP        $remote_addr;
        proxy_set_header   X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # Static assets — long cache, no rate limit
    location /assets/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_cache_valid  200 1d;
        add_header         Cache-Control "public, max-age=86400";
    }

    # SPA catch-all
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host             $host;
        proxy_set_header   X-Real-IP        $remote_addr;
        proxy_set_header   X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
SNIPPET

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/goodwe-guru
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ok "nginx configured with rate limiting and security headers"

# ── Let's Encrypt ────────────────────────────────────────────────────────────
if [[ -n "${DOMAIN:-}" && -z "${LE_EMAIL:-}" ]]; then
  warn "No Let's Encrypt email provided — skipping HTTPS. App is reachable over HTTP."
elif [[ -n "${DOMAIN:-}" ]]; then
  info "Obtaining Let's Encrypt certificate for $DOMAIN …"
  # certbot needs port 80 reachable from the internet for this domain; on a
  # purely internal LAN this will fail and we fall back to HTTP (IP still works).
  if certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL"; then

    # Append HTTPS server block with HSTS
    cat >> "$NGINX_CONF" <<NGINX_HTTPS

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # HSTS — browsers remember HTTPS for 2 years
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    include /etc/nginx/snippets/goodwe-proxy.conf;
}
NGINX_HTTPS

    nginx -t && systemctl reload nginx
    ok "HTTPS configured for $DOMAIN with HSTS"

    # Auto-renew via systemd timer (preferred over cron on Debian 13)
    systemctl enable --now certbot.timer 2>/dev/null \
      || (crontab -l 2>/dev/null; echo "0 3 */2 * * certbot renew --quiet && systemctl reload nginx") | crontab -
    ok "Let's Encrypt auto-renewal enabled"
  else
    warn "certbot failed — check DNS. Re-run: certbot --nginx -d $DOMAIN"
    warn "Continuing with HTTP-only config"
  fi
fi

# ── fail2ban — protect nginx ──────────────────────────────────────────────────
info "Configuring fail2ban …"
cat > /etc/fail2ban/jail.d/goodwe.conf <<'F2B'
[nginx-http-auth]
enabled  = true
port     = http,https
logpath  = /var/log/nginx/error.log
maxretry = 5
bantime  = 3600

[nginx-limit-req]
enabled  = true
port     = http,https
logpath  = /var/log/nginx/error.log
maxretry = 10
bantime  = 600
findtime = 60
filter   = nginx-limit-req

[nginx-botsearch]
enabled  = true
port     = http,https
logpath  = /var/log/nginx/access.log
maxretry = 2
bantime  = 86400
F2B

systemctl enable --now fail2ban
ok "fail2ban configured (nginx brute-force protection active)"

# ── Print summary ─────────────────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo -e "${GRN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GRN}║    GoodWe Guru installed successfully!     ║${NC}"
echo -e "${GRN}╚════════════════════════════════════════════╝${NC}"
echo ""
PORT_SFX=""; [[ "${HTTP_PORT:-80}" != 80 ]] && PORT_SFX=":${HTTP_PORT}"
echo -e "  ${BLU}Local URL   :${NC} http://${IP}${PORT_SFX}"
[[ -n "${DOMAIN:-}" ]] && echo -e "  ${BLU}Secure URL  :${NC} https://${DOMAIN}"
echo -e "  ${BLU}Password    :${NC} ${APP_PASSWORD:-<set in config.env>}"
${PASSWORD_GENERATED:-false} && echo -e "              ${YLW}(auto-generated — save it now)${NC}"
echo -e "  ${BLU}Config      :${NC} ${DATA_DIR}/config.env"
echo -e "  ${BLU}Logs        :${NC} journalctl -u ${SERVICE_NAME} -f"
echo -e "  ${BLU}Service     :${NC} systemctl status ${SERVICE_NAME}"
echo ""
echo -e "  To update to the latest code:"
echo -e "    goodwe-guru-update            (full)"
echo -e "    goodwe-guru-update --quick    (pull + restart only)"
echo ""
echo -e "  To change password / inverter IP:"
echo -e "    nano ${DATA_DIR}/config.env"
echo -e "    systemctl restart ${SERVICE_NAME}"
echo ""
echo -e "  Firewall status: ufw status"
echo -e "  Fail2ban bans:   fail2ban-client status nginx-limit-req"
echo ""
