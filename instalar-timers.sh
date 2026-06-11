#!/bin/bash
# Instala los resumenes automaticos (6:00 am y 10:00 pm) y reinicia el bot.
set -e
AQUI="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/systemd/user
cp "$AQUI"/agenda-resumen.service "$AQUI"/agenda-resumen.timer \
   "$AQUI"/agenda-noche.service "$AQUI"/agenda-noche.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agenda-resumen.timer agenda-noche.timer
systemctl --user restart agenda-bot.service
echo "Listo. Timers activos:"
systemctl --user list-timers agenda-resumen.timer agenda-noche.timer --no-pager
