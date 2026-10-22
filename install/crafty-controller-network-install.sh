#!/usr/bin/env bash
# Runs inside the new container; started by ct/crafty-controller-network.sh.
# Based on install/crafty-controller-install.sh by CrazyWolf13
# (community-scripts, MIT). The actual install is proxmox/install.sh, shared
# with plain (non community-scripts) containers.

# shellcheck disable=SC1090,SC1091
source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

COMMUNITY_SCRIPTS_URL="${COMMUNITY_SCRIPTS_URL:-https://raw.githubusercontent.com/Wkester25/crafty-controller-network/main}"

msg_info "Installing Crafty-Controller-Network (Patience)"
$STD bash -c "$(curl -fsSL "${COMMUNITY_SCRIPTS_URL}/proxmox/install.sh")"
msg_ok "Installed Crafty-Controller-Network"

CREDS_FILE="/opt/crafty-controller/crafty/crafty-4/app/config/default-creds.txt"
if [[ -f "$CREDS_FILE" ]]; then
  cat <<EOF >~/crafty-controller.creds
Crafty-Controller-Credentials
Username: $(grep -oP '(?<="username": ")[^"]*' "$CREDS_FILE")
Password: $(grep -oP '(?<="password": ")[^"]*' "$CREDS_FILE")
EOF
fi

motd_ssh
customize
cleanup_lxc
