#!/usr/bin/env bash
# One-time bootstrap of a fresh Ubuntu 24.04 box for Anchor.
# Run as root with the CI public key as the argument:
#   bash setup.sh "ssh-ed25519 AAAA... anchor-ci"
# Prompts for the R2 credentials (they live only on this box, in root's rclone config).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
[ $# -eq 1 ] || { echo "usage: bash setup.sh \"<CI public key line>\"" >&2; exit 1; }
ci_public_key="$1"
here="$(cd "$(dirname "$0")" && pwd)"

echo "==> installing docker, rclone, rsync, unattended-upgrades"
# The installers get their stdin nulled so the R2 prompts below still read ours
# when this script is fed its answers over ssh (see the deploy wizard).
export DEBIAN_FRONTEND=noninteractive
apt-get update -q </dev/null
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh </dev/null
fi
apt-get install -qy rclone rsync unattended-upgrades </dev/null

echo "==> enabling automatic security updates"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades

echo "==> creating the deploy user GitHub Actions logs in as"
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf '%s\n' "$ci_public_key" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "==> staging /opt/anchor (the deploy job rsyncs over this on every deploy)"
install -d -o deploy -g deploy /opt/anchor
cp "$here"/* /opt/anchor/
chown deploy:deploy /opt/anchor/*

echo "==> firewall: ssh and web only"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null

echo "==> ssh: keys only, no passwords"
printf 'PasswordAuthentication no\n' > /etc/ssh/sshd_config.d/50-anchor.conf
systemctl reload ssh

echo "==> backups: rclone remote for the R2 bucket"
read -rp "R2 account id: " r2_account
read -rp "R2 access key id: " r2_key
read -rsp "R2 secret access key: " r2_secret; echo
mkdir -p /root/.config/rclone
cat > /root/.config/rclone/rclone.conf <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${r2_key}
secret_access_key = ${r2_secret}
endpoint = https://${r2_account}.r2.cloudflarestorage.com
EOF
chmod 600 /root/.config/rclone/rclone.conf
echo "checking the bucket is reachable..."
rclone lsf r2:anchor-backups >/dev/null
echo "bucket ok"

echo "==> nightly backup timer"
install -m 644 /opt/anchor/anchor-backup.service /opt/anchor/anchor-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now anchor-backup.timer

echo "==> done. next: merge the deploy PR; the first push to main deploys here."
