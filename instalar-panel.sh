#!/bin/bash
# Pone el panel de estado (panel.py) en la pantalla fisica del equipo (tty1).
# Pensado para el Lenovo headless: al abrir la tapa ves RAM/CPU/disco/temp y el
# bot, con colores. Genera el servicio con la ruta/usuario REALES de esta maquina.
# Uso (en el equipo que muestra el panel):  sudo bash instalar-panel.sh
set -e
if [ "$(id -u)" -ne 0 ]; then
    echo "Corre con sudo:  sudo bash instalar-panel.sh"; exit 1
fi
AQUI="$(cd "$(dirname "$0")" && pwd)"
USUARIO="${SUDO_USER:-$USER}"
UID_USR="$(id -u "$USUARIO")"

cat > /etc/systemd/system/panel-asistente.service <<EOF
[Unit]
Description=Panel de estado del asistente en consola (tty1)
After=multi-user.target
Conflicts=getty@tty1.service

[Service]
Type=simple
User=$USUARIO
Environment=XDG_RUNTIME_DIR=/run/user/$UID_USR
Environment=TERM=linux
WorkingDirectory=$AQUI
ExecStart=/usr/bin/python3 $AQUI/panel.py
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

# tty1 lo usaba el login de texto; lo cedemos al panel.
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now panel-asistente.service
echo "Listo. El panel ya se ve en la pantalla (tty1)."
echo "Para volver al login normal:  sudo systemctl disable --now panel-asistente.service && sudo systemctl enable --now getty@tty1.service"
