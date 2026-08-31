#!/usr/bin/env bash
# One-time bootstrap of a fresh Ubuntu 24.04 box for Anchor.
# First run, as root, with the CI public key as the argument:
#   bash setup.sh "ssh-ed25519 AAAA... anchor-ci"
# Prompts for the R2 credentials (they live only on this box, in root's rclone config).
# Safe to re-run from /opt/anchor without arguments: it keeps the existing CI key and
# rclone config and refreshes everything else (including the systemd units).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
[ $# -le 1 ] || { echo "usage: bash setup.sh [\"<CI public key line>\"]" >&2; exit 1; }
ci_public_key="${1:-}"
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
apt-get install -qy rsync unzip unattended-upgrades </dev/null

# rclone comes from upstream, not apt: Ubuntu 24.04 packages 1.60.1 (2022), which
# fails R2 uploads with "501 Not Implemented" on the first attempt and only lands
# on rclone's internal retry. Pinned and checksummed so a rebuilt box gets the
# same binary, and a swapped artifact fails loudly instead of installing.
RCLONE_VERSION=v1.75.0
RCLONE_SHA256=aa2804e08f48250e71009c727124b6341cd0288465804a9a09d14663cabafbaa
# Drop the apt build if an earlier run of this script installed it, so /usr/bin and
# /usr/local/bin can never disagree about which rclone the backup timer gets.
if dpkg -s rclone >/dev/null 2>&1; then
  apt-get purge -qy rclone </dev/null
fi
installed_rclone="$(rclone version 2>/dev/null | head -n1 || true)"
if [[ "$installed_rclone" != "rclone $RCLONE_VERSION" ]]; then
  zip="/tmp/rclone-$RCLONE_VERSION-linux-amd64.zip"
  curl -fsSL -o "$zip" \
    "https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-linux-amd64.zip"
  printf '%s  %s\n' "$RCLONE_SHA256" "$zip" | sha256sum -c -
  unzip -oq "$zip" -d /tmp/rclone-unpack
  install -m 755 "/tmp/rclone-unpack/rclone-$RCLONE_VERSION-linux-amd64/rclone" /usr/local/bin/rclone
  rm -rf "$zip" /tmp/rclone-unpack
fi
rclone version | head -n1 || true

echo "==> enabling automatic security updates"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades

echo "==> creating the deploy user GitHub Actions logs in as"
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
if [[ -n "$ci_public_key" ]]; then
  printf '%s\n' "$ci_public_key" > /home/deploy/.ssh/authorized_keys
  chown deploy:deploy /home/deploy/.ssh/authorized_keys
  chmod 600 /home/deploy/.ssh/authorized_keys
elif [[ ! -s /home/deploy/.ssh/authorized_keys ]]; then
  echo "the first run needs the CI public key as the argument" >&2
  exit 1
fi

echo "==> staging /opt/anchor (the deploy job rsyncs over this on every deploy)"
install -d -o deploy -g deploy /opt/anchor
if [[ "$here" != /opt/anchor ]]; then
  cp "$here"/* /opt/anchor/
  chown deploy:deploy /opt/anchor/*
fi

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
if [[ -f /root/.config/rclone/rclone.conf ]]; then
  echo "keeping the existing rclone config"
else
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
no_check_bucket = true
EOF
  chmod 600 /root/.config/rclone/rclone.conf
fi
# Object-scoped R2 tokens cannot check or create buckets, and rclone attempts that
# before every upload unless told not to: without this, uploads fail 403 outright.
# Appended separately so a config written by an earlier run gets it too.
if ! grep -q '^no_check_bucket' /root/.config/rclone/rclone.conf; then
  printf 'no_check_bucket = true\n' >> /root/.config/rclone/rclone.conf
fi
echo "checking the bucket is reachable..."
rclone lsf r2:anchor-backups >/dev/null
echo "bucket ok"

echo "==> nightly backup timer"
install -m 644 /opt/anchor/anchor-backup.service /opt/anchor/anchor-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now anchor-backup.timer

echo "==> done. next: merge the deploy PR; the first push to main deploys here."
