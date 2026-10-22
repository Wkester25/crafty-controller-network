#!/usr/bin/env bash
# Proxmox VE helper (community-scripts framework) for this Crafty Controller
# fork. Run on the Proxmox HOST:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Wkester25/crafty-controller-network/main/ct/crafty-controller-network.sh)"
#
# Pointing COMMUNITY_SCRIPTS_URL at this repo makes the community engine load
# install/crafty-controller-network-install.sh from here, and makes the
# container's `update` command come back to this script.
export COMMUNITY_SCRIPTS_URL="${COMMUNITY_SCRIPTS_URL:-https://raw.githubusercontent.com/Wkester25/crafty-controller-network/main}"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
# shellcheck disable=SC1090
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Based on ct/crafty-controller.sh by CrazyWolf13 (community-scripts, MIT)
# Source: https://github.com/Wkester25/crafty-controller-network

APP="Crafty-Controller-Network"
var_tags="${var_tags:-gaming}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-4096}"
var_disk="${var_disk:-16}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-yes}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -d /opt/crafty-controller/crafty/crafty-4 ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  # install.sh updates in place: pulls the latest commit (or converts an
  # official Crafty install), keeps servers/database/settings, restarts.
  msg_info "Updating ${APP}"
  $STD bash -c "$(curl -fsSL "${COMMUNITY_SCRIPTS_URL}/proxmox/install.sh")"
  msg_ok "Updated ${APP}"
  msg_ok "Updated successfully!"
  exit
}

start
build_container
description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}https://${IP}:8443${CL}"
