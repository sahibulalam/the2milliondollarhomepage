#!/usr/bin/env bash
# Start the site, loading .env if it is there.
set -euo pipefail
cd "$(dirname "$0")"

# .env is READ, not sourced. Sourcing runs the file as shell, so a perfectly
# ordinary value -- SITE_TAGLINE=Two million pixels. A dollar each. -- becomes
# a command and the launcher dies. This takes each value literally.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    key=${key%"${key##*[![:space:]]}"}          # trim trailing space
    key=${key#"${key%%[![:space:]]*}"}          # trim leading space
    case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
    case "$val" in
      \"*\") val=${val#\"}; val=${val%\"} ;;    # "quoted, kept verbatim"
      \'*\') val=${val#\'}; val=${val%\'} ;;
      *)
        val=${val%%[[:space:]]#*}               # drop a trailing # comment
        val=${val%"${val##*[![:space:]]}"}      # trim trailing space
        val=${val#"${val%%[![:space:]]*}"}      # trim leading space
        ;;
    esac
    export "$key=$val"
  done < .env
fi

exec python3 app.py
