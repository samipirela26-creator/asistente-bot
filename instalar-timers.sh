#!/bin/bash
# Instala el bot + los resumenes automaticos (6:00 am y 10:00 pm).
# Genera las unidades de systemd con la ruta REAL de esta maquina, asi
# funciona igual en la Dell o en el Lenovo sin editar nada a mano.
# Uso (en la maquina que hospeda el bot):  bash instalar-timers.sh
set -e
AQUI="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/systemd/user

# --- Servicio principal del bot ---
cat > ~/.config/systemd/user/agenda-bot.service <<EOF
[Unit]
Description=Bot de agenda personal por Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$AQUI
ExecStart=/usr/bin/python3 $AQUI/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Nice=10
CPUWeight=20
MemoryMax=300M
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF

# --- Resumen matutino ---
cat > ~/.config/systemd/user/agenda-resumen.service <<EOF
[Unit]
Description=Resumen matutino del asistente

[Service]
Type=oneshot
WorkingDirectory=$AQUI
ExecStart=/usr/bin/python3 $AQUI/asistente.py resumen
EOF

# --- Resumen nocturno ---
cat > ~/.config/systemd/user/agenda-noche.service <<EOF
[Unit]
Description=Resumen nocturno del asistente

[Service]
Type=oneshot
WorkingDirectory=$AQUI
ExecStart=/usr/bin/python3 $AQUI/asistente.py noche
EOF

# Los timers no llevan rutas; se copian tal cual.
cp "$AQUI"/agenda-resumen.timer "$AQUI"/agenda-noche.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now agenda-bot.service agenda-resumen.timer agenda-noche.timer
systemctl --user restart agenda-bot.service
echo "Listo. El bot corre desde: $AQUI"
systemctl --user list-timers agenda-resumen.timer agenda-noche.timer --no-pager
