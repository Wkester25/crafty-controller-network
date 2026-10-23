#!/usr/bin/env bash
# Installs or updates this Crafty Controller fork on Debian 12/13 or
# Ubuntu 24.04 (e.g. a plain Proxmox LXC). Run as root inside the container:
#
#   bash install.sh
#
# Handles three cases:
#   * fresh system         -> installs Java, Python, the crafty user, the code
#                             and a systemd service
#   * community-scripts    -> swaps the official Crafty code for this fork,
#     Crafty container        keeping servers, database and settings
#   * this fork installed  -> updates the code to the latest commit
#
# Options (environment variables):
#   REPO, BRANCH       source repository and branch
#   JAVA_VERSIONS      Temurin JREs to install (default "8 11 17 21 25")
#   DEFAULT_JAVA       the one `java` points to (default 25)
#   GITHUB_TOKEN       only needed if the repo is private (read-only token);
#                      used for this run and never written to disk
#   FORCE=1            reinstall dependencies even when already up to date
#   AUTO_UPDATE=0      turn off the nightly automatic update (default on)
#   UPDATE_TIME        when the nightly update runs, container local time
#                      (default 04:00)
#
# Nightly updates run this script from a systemd timer
# (crafty-controller-update.timer). They do nothing unless a new version is
# on the branch, and never start Crafty if it was stopped.

set -euo pipefail

REPO="${REPO:-Wkester25/crafty-controller-network}"
BRANCH="${BRANCH:-main}"
JAVA_VERSIONS="${JAVA_VERSIONS:-8 11 17 21 25}"
DEFAULT_JAVA="${DEFAULT_JAVA:-25}"
AUTO_UPDATE="${AUTO_UPDATE:-1}"
UPDATE_TIME="${UPDATE_TIME:-04:00}"
# Set by the nightly timer, not by people
UNATTENDED="${UNATTENDED:-0}"
UPDATE_UNIT="crafty-controller-update"
# Created by the web UI to ask for an update; read by the path unit
UPDATE_REQUEST="/opt/crafty-controller/crafty/.update-request"
UPDATE_LOG="/opt/crafty-controller/crafty/last-update.log"
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
warn() { echo -e "\e[1;33mWarning:\e[0m $*" >&2; }
die() { echo -e "\e[1;31mError:\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this as root."
[[ -f /etc/debian_version ]] || die "This script supports Debian/Ubuntu only."

export DEBIAN_FRONTEND=noninteractive
ARCH="$(dpkg --print-architecture)"

# ------------------------------------------------------------------ token
REPO_URL="https://github.com/$REPO.git"
GIT_AUTH=(-c credential.helper=)
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  GIT_AUTH+=(-c "http.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)")
fi

git_auth() {
  # Never prompt: this often runs from `curl | bash` with no terminal
  GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" "$@"
}

# --------------------------------------------------------- system packages
install_packages() {
  info "Installing system packages"
  apt-get update -qq
  apt-get install -y -qq \
    git curl ca-certificates gnupg \
    python3 python3-dev python3-pip python3-venv \
    build-essential libffi-dev >/dev/null
}

install_java() {
  info "Installing Java (Temurin $JAVA_VERSIONS)"
  local codename
  # shellcheck disable=SC1091
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public |
    gpg --dearmor --yes -o /etc/apt/keyrings/adoptium.gpg
  echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $codename main" \
    >/etc/apt/sources.list.d/adoptium.list
  apt-get update -qq
  local version
  for version in $JAVA_VERSIONS; do
    apt-get install -y -qq "temurin-$version-jre" >/dev/null ||
      warn "Temurin $version is not available for $codename/$ARCH, skipped"
  done
  local default="/usr/lib/jvm/temurin-$DEFAULT_JAVA-jre-$ARCH/bin/java"
  if [[ -x "$default" ]]; then
    update-alternatives --set java "$default" >/dev/null
  else
    warn "Java $DEFAULT_JAVA not installed; leaving the system default java"
  fi
}

write_service() {
  info "Setting up the $SERVICE service"
  local java_path=""
  if [[ -d "/usr/lib/jvm/temurin-$DEFAULT_JAVA-jre-$ARCH/bin" ]]; then
    java_path="/usr/lib/jvm/temurin-$DEFAULT_JAVA-jre-$ARCH/bin:"
  fi
  cat >/etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Crafty 4
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CRAFTY_USER
WorkingDirectory=$APP_DIR
Environment=PATH=${java_path}$VENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$VENV/bin/python3 main.py -d
Restart=on-failure
# Give Minecraft servers time to save and stop
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q "$SERVICE"
}

setup_update_units() {
  # The update service is always installed: the web UI's "Update now" button
  # and the nightly timer both start it. Crafty runs as an unprivileged user,
  # so the button only creates $UPDATE_REQUEST; a root path unit watching
  # that file starts the update.
  [[ "$UPDATE_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] ||
    die "UPDATE_TIME must look like 04:00"

  # Updates must use the same repo/branch as this install
  cat >/etc/default/$UPDATE_UNIT <<EOF
REPO=$REPO
BRANCH=$BRANCH
EOF
  # Runs a copy so the update can replace install.sh while it executes.
  # Output goes to a file the web UI can show.
  cat >/etc/systemd/system/$UPDATE_UNIT.service <<EOF
[Unit]
Description=Update Crafty Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-/etc/default/$UPDATE_UNIT
Environment=UNATTENDED=1
ExecStartPre=/bin/rm -f $UPDATE_REQUEST
ExecStart=/bin/bash -c 'install -m 0700 $APP_DIR/proxmox/install.sh /run/$UPDATE_UNIT.sh && exec /bin/bash /run/$UPDATE_UNIT.sh'
StandardOutput=truncate:$UPDATE_LOG
StandardError=inherit
TimeoutStartSec=1h
EOF
  cat >/etc/systemd/system/$UPDATE_UNIT.path <<EOF
[Unit]
Description=Start a Crafty Controller update when the web UI asks for one

[Path]
PathExists=$UPDATE_REQUEST
Unit=$UPDATE_UNIT.service

[Install]
WantedBy=paths.target
EOF
  systemctl daemon-reload
  systemctl enable --now -q "$UPDATE_UNIT.path"

  if [[ "$AUTO_UPDATE" == "1" ]]; then
    cat >/etc/systemd/system/$UPDATE_UNIT.timer <<EOF
[Unit]
Description=Nightly update of Crafty Controller

[Timer]
OnCalendar=*-*-* $UPDATE_TIME:00

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable -q "$UPDATE_UNIT.timer"
    # Pick up a changed UPDATE_TIME
    systemctl restart "$UPDATE_UNIT.timer"
  elif [[ -f /etc/systemd/system/$UPDATE_UNIT.timer ]]; then
    info "Turning off nightly automatic updates"
    systemctl disable --now -q "$UPDATE_UNIT.timer" 2>/dev/null || true
    rm -f /etc/systemd/system/$UPDATE_UNIT.timer
    systemctl daemon-reload
  fi
}

# ------------------------------------------------------------------- code
FRESH=0
if [[ ! -d "$VENV" ]]; then
  FRESH=1
  info "No Crafty install found: doing a full install"
  install_packages
  install_java
  id "$CRAFTY_USER" &>/dev/null || useradd "$CRAFTY_USER" -m -s /bin/bash
  mkdir -p "$CRAFTY_ROOT"
  python3 -m venv "$VENV"
else
  command -v git &>/dev/null || install_packages
fi
id "$CRAFTY_USER" &>/dev/null || die "User '$CRAFTY_USER' does not exist."

# The checkout is owned by the crafty user; let root run git in it
if ! git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$APP_DIR"; then
  git config --system --add safe.directory "$APP_DIR"
fi

[[ "$UNATTENDED" == "1" ]] || setup_update_units

WAS_ACTIVE=0
systemctl is-active --quiet "$SERVICE" && WAS_ACTIVE=1

if [[ -d "$APP_DIR/.git" ]]; then
  # Follow the configured repo/branch even if an older install used others
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  git_auth -C "$APP_DIR" fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" ||
    die "Could not fetch $REPO ($BRANCH). If the repo is private, set GITHUB_TOKEN."
  if [[ "$(git -C "$APP_DIR" rev-parse HEAD)" == "$(git -C "$APP_DIR" rev-parse "origin/$BRANCH")" &&
    "${FORCE:-0}" != "1" ]] && [[ $WAS_ACTIVE -eq 1 || "$UNATTENDED" == "1" ]]; then
    info "Already up to date ($(git -C "$APP_DIR" log -1 --format='%h %s'))"
    exit 0
  fi
  info "Updating to the latest $BRANCH"
  systemctl stop "$SERVICE" 2>/dev/null || true
  # Runtime data is gitignored, so a forced checkout leaves it alone
  git -C "$APP_DIR" checkout --quiet --force -B "$BRANCH" "origin/$BRANCH"
else
  systemctl stop "$SERVICE" 2>/dev/null || true
  OLD_DIR=""
  if [[ -d "$APP_DIR" ]]; then
    OLD_DIR="$APP_DIR.official-$(date +%Y%m%d-%H%M%S)"
    info "Replacing the official Crafty code with $REPO"
    mv "$APP_DIR" "$OLD_DIR"
  else
    info "Downloading $REPO ($BRANCH)"
  fi
  if ! git_auth clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
    if [[ -n "$OLD_DIR" ]]; then
      mv "$OLD_DIR" "$APP_DIR"
      systemctl start "$SERVICE" 2>/dev/null || true
    fi
    die "Could not download $REPO ($BRANCH). If the repo is private, set GITHUB_TOKEN."
  fi
  if [[ -n "$OLD_DIR" ]]; then
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
mkdir -p /opt/crafty-controller/server
chown -R "$CRAFTY_USER:$CRAFTY_USER" /opt/crafty-controller

info "Installing Python dependencies (this can take a few minutes)"
runuser -u "$CRAFTY_USER" -- "$VENV/bin/pip" install --no-cache-dir -q --upgrade pip
runuser -u "$CRAFTY_USER" -- "$VENV/bin/pip" install --no-cache-dir -q -r "$APP_DIR/requirements.txt"

[[ -f /etc/systemd/system/$SERVICE.service ]] || write_service

if [[ "$UNATTENDED" == "1" && $WAS_ACTIVE -eq 0 ]]; then
  # Someone stopped Crafty on purpose; don't start it at 4am
  info "Updated to $(git -C "$APP_DIR" log -1 --format='%h %s'); Crafty was stopped, leaving it stopped"
  exit 0
fi

info "Starting Crafty"
systemctl start "$SERVICE"

CREDS_FILE="$APP_DIR/app/config/default-creds.txt"
if [[ $FRESH -eq 1 ]]; then
  for _ in $(seq 1 60); do
    [[ -f "$CREDS_FILE" ]] && break
    sleep 2
  done
fi
sleep 3
systemctl is-active --quiet "$SERVICE" ||
  die "Crafty did not start. Check: journalctl -u $SERVICE -n 50"

IP="$(hostname -I | awk '{print $1}')"
echo
info "Done. Crafty is running at https://$IP:8443"
echo "    Version: $(git -C "$APP_DIR" log -1 --format='%h %s')"
if [[ $FRESH -eq 1 && -f "$CREDS_FILE" ]]; then
  echo "    First login: see $CREDS_FILE"
fi
if [[ "$AUTO_UPDATE" == "1" ]]; then
  TZ_NAME="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)"
  echo "    Updates install automatically every night at $UPDATE_TIME ($TZ_NAME time)."
  if [[ "$TZ_NAME" == "UTC" || "$TZ_NAME" == "Etc/UTC" ]]; then
    echo "    That's UTC. For your local time: timedatectl set-timezone America/New_York (for example)"
  fi
else
  echo "    Update later by running this script again."
fi
