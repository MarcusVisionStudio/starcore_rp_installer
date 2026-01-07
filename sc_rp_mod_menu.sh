#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$DIR/config.env"
INSTALLER="$DIR/sc_rp_mod_installer.sh"
LOG_FILE="/tmp/sc_rp_menu.log"

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: Missing config.env at $CONFIG" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$CONFIG"

have_whiptail() {
  command -v whiptail >/dev/null 2>&1
}

status_line="Ready"

log_msg() {
  local msg="$1"
  printf "%s %s\n" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$msg" >>"$LOG_FILE"
}

logo_block() {
  cat <<'LOGO'
  _____ ______________  ____  ____  ____  ____
 / ___// ____/  _/ __ \/ __ \/ __ \/ __ \/ __ \
 \__ \/ /    / // /_/ / /_/ / /_/ / /_/ / /_/ /
___/ / /____/ // _, _/ ____/ _, _/ ____/ _, _/
/____/\____/___/_/ |_/_/   /_/ |_/_/   /_/ |_|

  StarCore RP Installer - Project Zomboid B42.13.1
LOGO
}

render_status() {
  printf "Status: %s" "$status_line"
}

escape_value() {
  local val="$1"
  if [[ "$val" =~ [[:space:]] || "$val" == *";"* || "$val" == *"#"* ]]; then
    printf '"%s"' "${val//"/\\\"}"
  else
    printf '%s' "$val"
  fi
}

update_env() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(escape_value "$value")"
  local tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$escaped" 'BEGIN{updated=0}
    $0 ~ "^"k"=" {print k"="v; updated=1; next}
    {print}
    END{if(!updated){print k"="v}}
  ' "$CONFIG" >"$tmp"
  mv "$tmp" "$CONFIG"
}

prompt_value() {
  local key="$1"
  local current="$2"
  local label="$3"
  local result=""
  if have_whiptail; then
    result=$(whiptail --title "Configure" --backtitle "$(render_status)" \
      --inputbox "$label" 10 70 "$current" 3>&1 1>&2 2>&3 || true)
  else
    echo "$label"
    read -r -p "[$current]: " result
    if [[ -z "$result" ]]; then
      result="$current"
    fi
  fi
  printf "%s" "$result"
}

reload_config() {
  # shellcheck disable=SC1091
  source "$CONFIG"
}

run_installer() {
  local action="$1"
  local listfile="$2"
  local extra_flags="$3"
  log_msg "Run $action $listfile $extra_flags"
  if [[ -n "$extra_flags" ]]; then
    "$INSTALLER" "$action" "$listfile" $extra_flags
  else
    "$INSTALLER" "$action" "$listfile"
  fi
  status_line="Last action '$action' finished (exit=$?)"
}

config_menu() {
  reload_config
  local key label current value
  local -a items=(
    "PZ_INI" "PZ INI path"
    "STEAMCMD" "SteamCMD path"
    "STEAM_WORKSHOP_DIR" "Workshop content dir"
    "SERVER_START" "Server start script"
    "SERVER_STOP" "Server stop script"
    "SERVER_LOG" "Server log path"
    "PZ_APPID" "Project Zomboid App ID"
    "SEP_WORKSHOP" "INI Workshop separator"
    "SEP_MODS" "INI Mods separator"
    "MAP_VANILLA" "Vanilla map name"
    "RULES_FILE" "Rules file path"
    "DEPS_SEARCH_PAGES" "Deps search pages"
    "DEPS_MAX_CANDIDATES" "Deps max candidates"
    "DEPS_CACHE_FILE" "Deps cache file"
    "DEP_HTTP_TIMEOUT" "Deps HTTP timeout"
  )

  local choices=()
  local i
  for ((i=0; i<${#items[@]}; i+=2)); do
    key="${items[i]}"
    label="${items[i+1]}"
    current="${!key:-}"
    choices+=("$key" "$label ($current)")
  done

  if have_whiptail; then
    local selected
    selected=$(whiptail --title "Configurator" --backtitle "$(render_status)" \
      --menu "Select a setting to edit" 20 78 12 \
      "${choices[@]}" 3>&1 1>&2 2>&3 || true)
    if [[ -z "$selected" ]]; then
      return
    fi
    key="$selected"
  else
    echo "Select setting to edit:"
    local idx=1
    for ((i=0; i<${#items[@]}; i+=2)); do
      echo "$idx) ${items[i]} - ${items[i+1]}"
      ((idx++))
    done
    read -r -p "Choice: " choice
    if [[ -z "$choice" ]]; then
      return
    fi
    key="${items[((choice-1)*2)]}"
  fi

  label=""
  current="${!key:-}"
  for ((i=0; i<${#items[@]}; i+=2)); do
    if [[ "${items[i]}" == "$key" ]]; then
      label="${items[i+1]}"
      break
    fi
  done

  value=$(prompt_value "$key" "$current" "$label")
  if [[ -n "$value" ]]; then
    update_env "$key" "$value"
    reload_config
    status_line="Updated $key"
    log_msg "Updated $key"
  fi
}

main_menu() {
  local listfile="$DIR/mods.list"
  local dry_run="--dry-run"

  while true; do
    if have_whiptail; then
      local selection
      selection=$(whiptail --title "StarCore RP Installer" \
        --backtitle "$(render_status) | Log: $LOG_FILE" \
        --menu "$(logo_block)\nChoose an action:" 20 78 10 \
        "apply" "Apply mods (dry-run)" \
        "apply_live" "Apply mods (live)" \
        "verify" "Verify workshop items" \
        "freshness" "Freshness check" \
        "report" "Generate report" \
        "config" "Configure paths" \
        "exit" "Exit" 3>&1 1>&2 2>&3 || true)
      case "$selection" in
        apply)
          run_installer apply "$listfile" "$dry_run"
          ;;
        apply_live)
          run_installer apply "$listfile" ""
          ;;
        verify)
          run_installer verify "$listfile" ""
          ;;
        freshness)
          run_installer freshness "$listfile" ""
          ;;
        report)
          run_installer report "$listfile" ""
          ;;
        config)
          config_menu
          ;;
        exit|"")
          break
          ;;
      esac
    else
      echo
      logo_block
      echo "$(render_status) | Log: $LOG_FILE"
      echo "1) Apply mods (dry-run)"
      echo "2) Apply mods (live)"
      echo "3) Verify workshop items"
      echo "4) Freshness check"
      echo "5) Generate report"
      echo "6) Configure paths"
      echo "7) Exit"
      read -r -p "Choice: " choice
      case "$choice" in
        1) run_installer apply "$listfile" "$dry_run" ;;
        2) run_installer apply "$listfile" "" ;;
        3) run_installer verify "$listfile" "" ;;
        4) run_installer freshness "$listfile" "" ;;
        5) run_installer report "$listfile" "" ;;
        6) config_menu ;;
        7) break ;;
      esac
    fi
  done
}

main_menu
