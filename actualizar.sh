#!/usr/bin/env bash
# Auto-actualiza el bot desde GitHub y lo reinicia SOLO si hubo cambios.
# Pensado para correr en un timer de systemd en la maquina que hospeda el bot
# (ej. el Lenovo). Seguro: si el codigo nuevo no arranca, no reinicia.
set -euo pipefail
cd "$(dirname "$0")"

# 1) Trae lo ultimo sin tocar el arbol de trabajo.
git fetch --quiet origin || { echo "$(date '+%F %T') sin red; reintento luego"; exit 0; }

# 2) Si ya estamos al dia, no hay nada que hacer.
if [ "$(git rev-parse @)" = "$(git rev-parse @{u})" ]; then
    exit 0
fi

# 3) Actualiza (solo avance rapido; esta maquina no hace commits propios).
git pull --quiet --ff-only

# 4) Red de seguridad: que el bot al menos importe antes de reiniciar.
#    Asi un push roto no deja el bot caido.
if python3 -c "import bot" 2>/tmp/agenda-update.log; then
    systemctl --user restart agenda-bot.service
    echo "$(date '+%F %T') actualizado y bot reiniciado"
else
    echo "$(date '+%F %T') ERROR: el codigo nuevo no importa; NO reinicio." >&2
    echo "Detalle en /tmp/agenda-update.log" >&2
    exit 1
fi
