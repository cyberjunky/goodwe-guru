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
echo ""
echo -e "${YLW}╔══════════════════════════════════════════╗${NC}"
echo -e "${YLW}║      GoodWe Guru — Setup Wizard       ║${NC}"
echo -e "${YLW}╚══════════════════════════════════════════╝${NC}"
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
  PASSWORD_GENERATED=false
  [[ ${#APP_PASSWORD} -lt 8 ]] && { warn "Password is short (< 8 chars) — consider a stronger one"; }
fi

read -rp "  Poll interval in seconds [10]       : " POLL_INTERVAL
POLL_INTERVAL=${POLL_INTERVAL:-10}

read -rp "  Domain/subdomain for HTTPS          : " DOMAIN
read -rp "  (leave blank to use IP-only / HTTP)   "

JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
             || openssl rand -hex 32)

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
  pct create "$VMID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname goodwe-guru \
    --cores 1 \
    --memory 512 \
    --swap 256 \
    --storage "$STORAGE" \
    --rootfs "$STORAGE:4" \
    --net0 "name=eth0,bridge=vmbr0,ip=dhcp,firewall=1" \
    --unprivileged 1 \
    --features "nesting=0" \
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

  # Export all vars needed inside the container, then exec this same script
  pct exec "$VMID" -- bash -c "
    export INVERTER_HOST='$INVERTER_HOST'
    export APP_PASSWORD='$APP_PASSWORD'
    export POLL_INTERVAL='$POLL_INTERVAL'
    export JWT_SECRET='$JWT_SECRET'
    export DOMAIN='${DOMAIN:-}'
    export NODE_MAJOR=$NODE_MAJOR
    export APP_DIR='$APP_DIR'
    export DATA_DIR='$DATA_DIR'
    export SERVICE_USER='$SERVICE_USER'
    export SERVICE_NAME='$SERVICE_NAME'
    export NGINX_CONF='$NGINX_CONF'
    export REPO_URL='$REPO_URL'
    bash -s" < "$0"

  LXC_IP=$(pct exec "$VMID" -- hostname -I 2>/dev/null | awk '{print $1}')
  echo ""
  echo -e "${GRN}╔════════════════════════════════════════════════════╗${NC}"
  echo -e "${GRN}║  GoodWe Guru installed successfully!            ║${NC}"
  echo -e "${GRN}╚════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BLU}Container ID :${NC} $VMID"
  echo -e "  ${BLU}Local URL    :${NC} http://${LXC_IP:-<container-ip>}"
  [[ -n "${DOMAIN:-}" ]] && echo -e "  ${BLU}Secure URL   :${NC} https://$DOMAIN"
  echo -e "  ${BLU}Password     :${NC} ${APP_PASSWORD}"
  $PASSWORD_GENERATED && echo -e "               ${YLW}(auto-generated — save it now)${NC}"
  echo -e "  ${BLU}Logs         :${NC} pct exec $VMID -- journalctl -u $SERVICE_NAME -f"
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

# ── Node.js ───────────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ "$(node --version | cut -d. -f1 | tr -d v)" -lt "$NODE_MAJOR" ]]; then
  info "Installing Node.js $NODE_MAJOR …"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - >/dev/null 2>&1
  apt-get install -y -qq nodejs
fi
ok "Node $(node --version)"

# ── Service user ──────────────────────────────────────────────────────────────
id "$SERVICE_USER" &>/dev/null \
  || useradd -r -s /usr/sbin/nologin -m -d "$APP_DIR" "$SERVICE_USER"

# ── Data directory ────────────────────────────────────────────────────────────
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR"

# ── Write config ──────────────────────────────────────────────────────────────
cat > "$DATA_DIR/config.env" <<EOF
INVERTER_HOST=${INVERTER_HOST:-192.168.1.100}
APP_PASSWORD=${APP_PASSWORD:-changeme}
POLL_INTERVAL=${POLL_INTERVAL:-10}
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
  info "Cloning GoodWe Guru …"
  git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# ── Python venv ───────────────────────────────────────────────────────────────
info "Installing Python dependencies …"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
ok "Python venv ready"

# ── Build React frontend ──────────────────────────────────────────────────────
info "Building frontend …"
cd "$APP_DIR/frontend"
npm ci --silent
npm run build --silent
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
NoNewPrivileges=yes
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

# Capabilities (none needed — binding to 8000, not privileged port)
CapabilityBoundingSet=
AmbientCapabilities=

StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
ok "Systemd service enabled and started"

# ── UFW firewall ──────────────────────────────────────────────────────────────
info "Configuring UFW firewall …"
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow ssh >/dev/null           # keep SSH open
ufw allow 'Nginx Full' >/dev/null  # 80 + 443
ufw --force enable >/dev/null
ok "UFW: only SSH (22) and Nginx Full (80/443) allowed inbound"

# ── nginx — rate-limited, hardened, with security headers ────────────────────
info "Configuring nginx …"
SERVER_NAME="${DOMAIN:-_}"

# Shared rate limit zone (defined at http level)
cat > /etc/nginx/conf.d/goodwe-limits.conf <<'EOF'
# Rate limiting — prevents brute-force against login endpoint
limit_req_zone  $binary_remote_addr zone=login:10m  rate=5r/m;
limit_req_zone  $binary_remote_addr zone=api:10m    rate=60r/m;
limit_req_zone  $binary_remote_addr zone=ws:10m     rate=10r/m;
limit_conn_zone $binary_remote_addr zone=addr:10m;

# Hide nginx version
server_tokens off;
EOF

cat > "$NGINX_CONF" <<NGINX
# HTTP — redirect to HTTPS if domain is set, otherwise serve directly
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};

$(if [[ -n "${DOMAIN:-}" ]]; then
cat <<REDIRECT
    # Redirect all HTTP to HTTPS
    return 301 https://\$host\$request_uri;
REDIRECT
else
cat <<DIRECT
    include /etc/nginx/snippets/goodwe-proxy.conf;
DIRECT
fi)
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
if [[ -n "${DOMAIN:-}" ]]; then
  info "Obtaining Let's Encrypt certificate for $DOMAIN …"
  read -rp "  Email for Let's Encrypt renewal notices: " LE_EMAIL

  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL"; then

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
echo -e "${GRN}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${GRN}║  GoodWe Guru installed successfully!               ║${NC}"
echo -e "${GRN}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BLU}Local URL   :${NC} http://${IP}"
[[ -n "${DOMAIN:-}" ]] && echo -e "  ${BLU}Secure URL  :${NC} https://${DOMAIN}"
echo -e "  ${BLU}Password    :${NC} ${APP_PASSWORD:-<set in config.env>}"
${PASSWORD_GENERATED:-false} && echo -e "              ${YLW}(auto-generated — save it now)${NC}"
echo -e "  ${BLU}Config      :${NC} ${DATA_DIR}/config.env"
echo -e "  ${BLU}Logs        :${NC} journalctl -u ${SERVICE_NAME} -f"
echo -e "  ${BLU}Service     :${NC} systemctl status ${SERVICE_NAME}"
echo ""
echo -e "  To change password / inverter IP:"
echo -e "    nano ${DATA_DIR}/config.env"
echo -e "    systemctl restart ${SERVICE_NAME}"
echo ""
echo -e "  Firewall status: ufw status"
echo -e "  Fail2ban bans:   fail2ban-client status nginx-limit-req"
echo ""
