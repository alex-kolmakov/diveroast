#!/usr/bin/env bash
# Initial setup script for a fresh Hetzner CX22 running Debian/Ubuntu.
# Run once as root: bash setup-vps.sh
set -euo pipefail

REPO_URL="https://github.com/alex-kolmakov/divelog-autoreport.git"
APP_DIR="/opt/diveroast"

echo "==> Updating system packages"
apt-get update -q && apt-get upgrade -yq

echo "==> Installing Docker"
apt-get install -yq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "==> Enabling Docker service"
systemctl enable docker
systemctl start docker

echo "==> Cloning repository"
mkdir -p "$APP_DIR"
git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"

echo "==> Creating SSL certificate directory"
mkdir -p /etc/ssl/cloudflare
echo ""
echo "  ACTION REQUIRED: Place your Cloudflare Origin Certificate files at:"
echo "    /etc/ssl/cloudflare/cert.pem"
echo "    /etc/ssl/cloudflare/key.pem"
echo "  Get them from: Cloudflare Dashboard → SSL/TLS → Origin Server → Create Certificate"
echo ""

echo "==> Creating .env from sample"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.prod.sample" "$APP_DIR/.env"
  echo "  ACTION REQUIRED: Edit $APP_DIR/.env and set GEMINI_API_KEY"
fi

echo "==> Setting up systemd service for auto-start on reboot"
cat > /etc/systemd/system/diveroast.service <<EOF
[Unit]
Description=DiveRoast Docker Compose
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d --build
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable diveroast

echo ""
echo "==> Setup complete. Next steps:"
echo "  1. Add Cloudflare Origin Certificate to /etc/ssl/cloudflare/"
echo "  2. Edit $APP_DIR/.env (set GEMINI_API_KEY, ALLOWED_ORIGINS)"
echo "  3. cd $APP_DIR && docker compose -f docker-compose.prod.yml up -d --build"
echo "  4. Point your domain DNS A record to this server's IP in Cloudflare"
