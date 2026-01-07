#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$DIR/config.env"

INSTALLER="$DIR/sc_rp_mod_installer.sh"
LIST_DEFAULT="$DIR/mods.list"
LOG="/tmp/rp_installer_wizard.log"

need() { command -v "$1" >/dev/null 2>&1; }

if ! need whiptail; then
  echo "[ERR] whiptail není nainstalovaný. Dej: apt install -y whiptail"
  exit 1
fi

pick_list() {
  local lf
  lf="$(whiptail --inputbox "Cesta k mods.list" 10 80 "$LIST_DEFAULT" 3>&1 1>&2 2>&3)" || exit 0
  echo "$lf"
}

show_file() {
  local title="$1"; local file="$2"
  if [[ -f "$file" ]]; then
    whiptail --textbox "$file" 25 100 --title "$title"
  else
    whiptail --msgbox "Soubor neexistuje: $file" 10 80
  fi
}

run_with_tailbox() {
  local title="$1"; shift
  : > "$LOG"

  # Spustit na pozadí, logovat do souboru
  ( set -o pipefail; "$@" 2>&1 | tee -a "$LOG" ) &
  local pid=$!

  # živý log okno
  whiptail --title "$title" --tailbox "$LOG" 25 100

  # po zavření tailboxu počkáme na konec procesu
  wait "$pid" || return $?
  return 0
}

sanitize_semilist() {
  # normalize separators to ';' and trim extra ';'
  echo "$1" \
    | sed -E 's/\r//g' \
    | tr ', ' ';;' \
    | tr -s ';' ';' \
    | sed -E 's/^;+//; s/;+$//'
}

sanitize_workshop_ids() {
  sanitize_semilist "$1" \
    | tr ';' '\n' \
    | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' \
    | sed -E 's/^workshopitems=//I' \
    | grep -E '^[0-9]+$' \
    | paste -sd';' -
}

sanitize_mod_ids() {
  sanitize_semilist "$1" \
    | tr ';' '\n' \
    | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' \
    | sed -E 's/^mods=//I' \
    | grep -E '^[A-Za-z0-9_.-]+$' \
    | grep -vi '^mods$' \
    | paste -sd';' -
}

parse_pairs_slash_format() {
  # input: "3346506593/Erikas_Tiles;3363546437/SomeMod;..."
  local input
  input="$(sanitize_semilist "$1")"

  local ws mods
  ws="$(echo "$input" | tr ';' '\n' | awk -F'/' 'NF>=1{print $1}' | grep -E '^[0-9]+$' | paste -sd';' -)"
  mods="$(echo "$input" | tr ';' '\n' | awk -F'/' 'NF>=2{print $2}' | grep -E '^[A-Za-z0-9_.-]+$' | paste -sd';' -)"

  echo "$ws" $'\n' "$mods"
}


main_menu() {
  whiptail --title "StarCore RP Mod Installer - Wizard" --menu "Vyber akci:" 18 90 10 \
    "1" "Edit mods.list (nano)" \
    "2" "Verify (najde ModID z mod.info)" \
    "3" "Freshness (stáří update z workshopu)" \
    "4" "Apply (STOP -> stáhnout -> zapsat INI -> START)" \
    "5" "Show INI keys (WorkshopItems/Mods/Map)" \
    "6" "Show last log" \
    "0" "Exit" \
    3>&1 1>&2 2>&3
}

while true; do
  choice="$(main_menu || true)"
  case "${choice:-0}" in
    1)
      lf="$(pick_list)"
      nano "$lf"
      ;;
    2)
      lf="$(pick_list)"
      run_with_tailbox "Verify" "$INSTALLER" verify "$lf"
      ;;
    3)
      lf="$(pick_list)"
      run_with_tailbox "Freshness" "$INSTALLER" freshness "$lf"
      ;;
    4)
      lf="$(pick_list)"
      if whiptail --yesno "Potvrdit APPLY?\n\n- Stop server\n- SteamCMD download/update\n- Přepsat WorkshopItems/Mods v INI\n- Start server\n\nList: $lf\nINI: $PZ_INI" 18 90; then
        run_with_tailbox "Apply" "$INSTALLER" apply "$lf"
        whiptail --msgbox "Hotovo.\n\nZkontroluj INI:\n$PZ_INI\n\nLog:\n$LOG" 14 80
      fi
      ;;
    5)
      if [[ -f "$PZ_INI" ]]; then
        grep -nE '^(WorkshopItems|Mods|Map)=' "$PZ_INI" > /tmp/rp_ini_keys.txt || true
        show_file "INI keys" /tmp/rp_ini_keys.txt
      else
        whiptail --msgbox "INI neexistuje: $PZ_INI" 10 80
      fi
      ;;
    6)
      show_file "Last log" "$LOG"
      ;;
    0|*)
      exit 0
      ;;
  esac
done
