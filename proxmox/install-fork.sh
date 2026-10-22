#!/usr/bin/env bash
# Switches a community-scripts Crafty-Controller LXC to this fork, or updates
# an LXC that already runs it. Run INSIDE the container as root:
#
#   bash install-fork.sh
#
# First create the container on the Proxmox host with:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/crafty-controller.sh)"
#
# The repo is private, so this asks for a GitHub token with read access to it
# (fine-grained token: Repository access -> this repo, Contents -> Read-only).
# The token is only used for this run and is never written to disk.
#
# Do NOT use the community script's "update" option afterwards: it replaces the
# code with the official Crafty release. Re-run this script to update instead.

set -euo pipefail

REPO="${REPO:-Wkester25/crafty-controller-network}"
BRANCH="${BRANCH:-feature/upnp-cloudflare}"
CRAFTY_ROOT="/opt/crafty-controller/crafty"
APP_DIR="$CRAFTY_ROOT/crafty-4"
VENV="$CRAFTY_ROOT/.venv"
SERVICE="crafty-controller"
CRAFTY_USER="crafty"

# Runtime data that lives inside the code dir and must survive a code swap
KEEP=(
  app/config/db
  app/config/web
  app/config/bin
  app/config/config.json
  app/config/network.json
  app/config/default-creds.txt
  app/migrations/status
  servers
  backups
  import
  logs
)

info() { echo -e "\e[1;34m==>\e[0m $*"; }
die() { echo -e "\e[1;31mError:\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this as root inside the Crafty container."
[[ -d "$VENV" ]] || die "$VENV not found. Create the container with the community crafty-controller script first."
id "$CRAFTY_USER" &>/dev/null || die "User '$CRAFTY_USER' does not exist."

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  read -rsp "GitHub token with read access to $REPO: " GITHUB_TOKEN
  echo
fi
[[ -n "$GITHUB_TOKEN" ]] || die "A token is required for a private repo."
AUTH_HEADER="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)"
REPO_URL="https://github.com/$REPO.git"

git_auth() {
  git -c "http.extraHeader=$AUTH_HEADER" "$@"
}

if ! command -v git &>/dev/null; then
  info "Installing git"
  apt-get update -qq
  apt-get install -y -qq git >/dev/null
fi

# The checkout is owned by the crafty user; let root run git in it
if ! git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$APP_DIR"; then
  git config --system --add safe.directory "$APP_DIR"
fi

info "Stopping Crafty"
systemctl stop "$SERVICE" || true

if [[ -d "$APP_DIR/.git" ]]; then
  info "Updating existing checkout to origin/$BRANCH"
  cd "$APP_DIR"
  git_auth fetch --quiet origin "$BRANCH"
  # Runtime data is gitignored, so a hard reset leaves it alone
  git reset --quiet --hard "origin/$BRANCH"
else
  info "Replacing the official Crafty code with $REPO ($BRANCH)"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  OLD_DIR="$APP_DIR.official-$STAMP"
  if [[ -d "$APP_DIR" ]]; then
    mv "$APP_DIR" "$OLD_DIR"
  fi
  if ! git_auth clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
    [[ -d "$OLD_DIR" ]] && mv "$OLD_DIR" "$APP_DIR"
    systemctl start "$SERVICE" || true
    die "Clone failed. Check the token can read $REPO. The original install was restored."
  fi
  if [[ -d "$OLD_DIR" ]]; then
    info "Carrying over servers, database and settings"
    for path in "${KEEP[@]}"; do
      if [[ -e "$OLD_DIR/$path" ]]; then
        mkdir -p "$(dirname "$APP_DIR/$path")"
        rm -rf "${APP_DIR:?}/$path"
        cp -a "$OLD_DIR/$path" "$APP_DIR/$path"
      fi
    done
    echo "    Previous code kept at $OLD_DIR (delete it once everything works)."
  fi
fi

# Never leave the token in the repo config
git -C "$APP_DIR" remote set-url origin "$REPO_URL"

chown -R "$CRAFTY_USER:$CRAFTY_USER" /opt/crafty-controller

info "Installing Python dependencies (this can take a few minutes)"
sudo -u "$CRAFTY_USER" bash -c "
  source '$VENV/bin/activate'
  cd '$APP_DIR'
  pip3 install --no-cache-dir -q -r requirements.txt
"

info "Starting Crafty"
systemctl start "$SERVICE"
sleep 5
if systemctl is-active --quiet "$SERVICE"; then
  IP="$(hostname -I | awk '{print $1}')"
  info "Done. Crafty is running at https://$IP:8443 (Panel Config -> Network)."
  echo "    Version: $(git -C "$APP_DIR" log -1 --format='%h %s')"
else
  die "Crafty did not start. Check: journalctl -u $SERVICE -n 50"
fi
