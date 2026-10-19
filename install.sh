#!/usr/bin/env bash
# MedusaHC KlipperScreen add-on installer
#
# Links the MedusaHC panel and its menu into an existing KlipperScreen install.
# Nothing in the KlipperScreen repository is modified: the panel is a symlink
# that git is told to ignore, so KlipperScreen keeps updating normally.

set -euo pipefail

addon_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printer_config="$HOME/printer_data/config"
klipperscreen_dir="$HOME/KlipperScreen"
restart=false
uninstall=false

include_line="[include MedusaHC_menu.conf]"
do_not_edit="#~# --- Do not edit below this line. This section is auto generated --- #~#"

info() { echo "[INFO] $*"; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

show_help() {
  cat <<EOF
Usage: $0 [-p <printer_config>] [-k <klipperscreen_dir>] [-r] [-u] [-h]
  -p  Directory holding KlipperScreen.conf (default: \$HOME/printer_data/config)
  -k  KlipperScreen directory (default: \$HOME/KlipperScreen)
  -r  Restart KlipperScreen when done
  -u  Uninstall (keeps MedusaHC_screen.conf)
  -h  Show this help
EOF
}

while getopts "p:k:ruh" arg; do
  case $arg in
    p) printer_config=$OPTARG ;;
    k) klipperscreen_dir=$OPTARG ;;
    r) restart=true ;;
    u) uninstall=true ;;
    h) show_help; exit 0 ;;
    *) show_help; exit 1 ;;
  esac
done

conf_file="$printer_config/KlipperScreen.conf"
panel_link="$klipperscreen_dir/panels/medusahc.py"
menu_link="$printer_config/MedusaHC_menu.conf"
settings_file="$printer_config/MedusaHC_screen.conf"
exclude_file="$klipperscreen_dir/.git/info/exclude"

checks() {
  [ "$EUID" -ne 0 ] || fail "Do not run this script as root."
  [ -d "$klipperscreen_dir/panels" ] || fail "KlipperScreen not found at $klipperscreen_dir (use -k)."
  [ -f "$conf_file" ] || fail "KlipperScreen.conf not found at $conf_file (use -p)."
  if [ -e "$panel_link" ] && [ ! -L "$panel_link" ]; then
    fail "$panel_link is a regular file, so a KlipperScreen build that already ships the MedusaHC panel is installed. Switch it back to stock KlipperScreen first."
  fi
}

add_include() {
  if grep -Fxq "$include_line" "$conf_file"; then
    info "$conf_file already includes MedusaHC_menu.conf."
    return
  fi
  local tmp
  tmp=$(mktemp)
  # Insert before KlipperScreen's auto-generated block, or append when there is none
  awk -v inc="$include_line" -v marker="$do_not_edit" '
    $0 == marker && !done { print inc; done = 1 }
    { print }
    END { if (!done) print inc }
  ' "$conf_file" > "$tmp"
  cat "$tmp" > "$conf_file"
  rm -f "$tmp"
  info "Added $include_line to $conf_file."
}

remove_include() {
  local tmp
  tmp=$(mktemp)
  grep -Fxv "$include_line" "$conf_file" > "$tmp" || true
  cat "$tmp" > "$conf_file"
  rm -f "$tmp"
  info "Removed $include_line from $conf_file."
}

install_files() {
  ln -sfn "$addon_dir/KlipperScreen/medusahc.py" "$panel_link"
  ln -sfn "$addon_dir/KlipperScreen/MedusaHC_menu.conf" "$menu_link"
  if [ ! -f "$settings_file" ]; then
    cp "$addon_dir/KlipperScreen/MedusaHC_screen.conf" "$settings_file"
    info "Created $settings_file."
  else
    info "Kept existing $settings_file."
  fi
  if [ -f "$exclude_file" ] && ! grep -Fxq "panels/medusahc.py" "$exclude_file"; then
    echo "panels/medusahc.py" >> "$exclude_file"
  fi
  info "Linked the panel and menu."
}

uninstall_files() {
  remove_include
  rm -f "$menu_link"
  if [ -L "$panel_link" ]; then
    rm -f "$panel_link"
  fi
  info "Uninstalled. $settings_file was left in place."
}

restart_klipperscreen() {
  if $restart; then
    info "Restarting KlipperScreen..."
    sudo systemctl restart KlipperScreen
  else
    info "Restart KlipperScreen to apply: sudo systemctl restart KlipperScreen"
  fi
}

if $uninstall; then
  uninstall_files
  restart_klipperscreen
  exit 0
fi

checks
install_files
add_include
info "MedusaHC KlipperScreen add-on installed."
restart_klipperscreen
