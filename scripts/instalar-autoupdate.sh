#!/bin/bash
# Instala la AUTO-ACTUALIZACION del bot desde GitHub.
# Genera las unidades de systemd con la ruta REAL de esta maquina, asi
# funciona igual en la Dell o en el Lenovo sin editar nada a mano.
# Uso (en la maquina que hospeda el bot):  bash scripts/instalar-autoupdate.sh
set -e
# Este script vive en scripts/; AQUI es la raiz del repo (un nivel arriba).
# actualizar.sh en sí se queda en la raiz (no en scripts/): es el ExecStart ya
# grabado en la unidad systemd instalada, y moverlo la rompería sin forma de
# auto-repararse.
AQUI="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$AQUI/actualizar.sh"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/agenda-actualizar.service <<EOF
[Unit]
Description=Auto-actualizar el bot de agenda desde GitHub
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$AQUI
ExecStart=$AQUI/actualizar.sh
EOF

cat > ~/.config/systemd/user/agenda-actualizar.timer <<EOF
[Unit]
Description=Revisa GitHub por actualizaciones del bot

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agenda-actualizar.timer
echo "Auto-actualizacion activa (revisa cada 10 min). Proxima ejecucion:"
systemctl --user list-timers agenda-actualizar.timer --no-pager
