#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$DIR/config.env"

PY="$DIR/tools/rp_mod_installer.py"
STATE="/tmp/sc_rp_apply_state.json"

usage() {
  echo "Usage:"
  echo "  $0 verify <mods.list>"
  echo "  $0 freshness <mods.list>"
  echo "  $0 apply <mods.list> [--no-restart]"
  echo "  $0 report <mods.list>"
  exit 1
}

cmd="${1:-}"
listfile="${2:-}"

# Be forgiving: if user forgets the list file, default to bundled mods.list.
if [[ -z "$cmd" ]]; then usage; fi

if [[ -z "$listfile" ]]; then
  listfile="$DIR/mods.list"
fi

# If a typo is provided (e.g. mod.list instead of mods.list), try the bundled one.
if [[ ! -f "$listfile" && -f "$DIR/mods.list" ]]; then
  echo "WARN: list file '$listfile' not found - using '$DIR/mods.list'" >&2
  listfile="$DIR/mods.list"
fi

if [[ ! -f "$listfile" ]]; then
  echo "ERROR: list file '$listfile' not found" >&2
  echo "Tip: run: ls -l '$DIR'" >&2
  exit 2
fi

restart="true"
if [[ "${3:-}" == "--no-restart" ]]; then restart="false"; fi

case "$cmd" in
  verify)
    python3 "$PY" verify --list "$listfile" --workshop-dir "$STEAM_WORKSHOP_DIR"
    ;;
  freshness)
    python3 "$PY" freshness --list "$listfile" --stale-warn "$STALE_DAYS_WARN" --stale-fail "$STALE_DAYS_FAIL"
    ;;
  apply)
    python3 "$PY" freshness --list "$listfile" --stale-warn "$STALE_DAYS_WARN" --stale-fail "$STALE_DAYS_FAIL"

    if [[ -x "$SERVER_STOP" ]]; then
      echo "[Installer] Stopping server: $SERVER_STOP"
      "$SERVER_STOP" || true
    else
      echo "[Installer][WARN] Stop script not executable: $SERVER_STOP (skipping stop)"
    fi

    auto_map_args=()
    if [[ "${AUTO_MAP:-0}" == "1" ]]; then
      auto_map_args=(--auto-map --map-vanilla "$MAP_VANILLA")
    fi

    rules_args=()
    if [[ -n "${RULES_FILE:-}" && -f "${RULES_FILE:-}" ]]; then
      rules_args=(--rules "$RULES_FILE")
      if [[ "${STRICT_CATEGORIES:-0}" == "1" ]]; then
        rules_args+=(--strict-categories)
      fi
    fi

    resolve_args=()
    if [[ "${RESOLVE_DEPS:-1}" == "1" ]]; then
      resolve_args=(--resolve-deps --dep-cache "$DIR/rules/modid_map.json")
    fi

    python3 "$PY" apply \
      --list "$listfile" \
      --steamcmd "$STEAMCMD" \
      --pz-appid "$PZ_APPID" \
      --workshop-dir "$STEAM_WORKSHOP_DIR" \
      --ini "$PZ_INI" \
      --sep-workshop "$SEP_WORKSHOP" \
      --sep-mods "$SEP_MODS" \
      --state-out "$STATE" \
      "${resolve_args[@]}" \
      "${auto_map_args[@]}" \
      "${rules_args[@]}"

    if [[ "$restart" == "true" ]]; then
      if [[ -x "$SERVER_START" ]]; then
        echo "[Installer] Starting server: $SERVER_START"
        "$SERVER_START"
      else
        echo "[Installer][WARN] Start script not executable: $SERVER_START (skipping start)"
      fi

      if [[ "${ROLLBACK_ON_ERRORS:-0}" == "1" ]]; then
        echo "[Installer] Waiting 12s then checking server log..."
        sleep 12

        if python3 "$PY" checklog --log "$SERVER_LOG" --tail 600 --patterns "$ROLLBACK_PATTERNS"; then
          echo "[Installer] Log check OK."
        else
          echo "[Installer][ROLLBACK] Errors detected. Restoring INI backup and restarting..."
          if [[ -f "$STATE" ]]; then
            backup="$(python3 -c 'import json;print(json.load(open("'"$STATE"'"))["backup"])')"
            if [[ -f "$backup" ]]; then
              cp -a "$backup" "$PZ_INI"
              echo "[Installer][ROLLBACK] Restored: $backup -> $PZ_INI"
              if [[ -x "$SERVER_START" ]]; then
                "$SERVER_START"
              fi
            else
              echo "[Installer][ROLLBACK][ERR] Backup not found: $backup"
              exit 4
            fi
          else
            echo "[Installer][ROLLBACK][ERR] State file missing: $STATE"
            exit 4
          fi
          exit 5
        fi
      fi
    fi
    ;;
  report)
    mkdir -p "$DIR/reports"
    python3 "$PY" report --list "$listfile" --out "$DIR/reports/report_$(date +%Y%m%d-%H%M%S).json"
    ;;
  *)
    usage
    ;;
esac
