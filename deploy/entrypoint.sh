#!/usr/bin/env sh
set -eu
# Offset lives on the /data volume so restarts don't re-fetch ~24h of updates.
exec python -m scanner.bot --serve --state "${SQZDOTS_STATE_PATH:-/data/telegram_state.json}"
