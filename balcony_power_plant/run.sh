#!/usr/bin/with-contenv bashio
# BalconyPowerPlant add-on entry point
bashio::log.info "Starting BalconyPowerPlant bridge ..."
exec python3 -u /app/main.py
