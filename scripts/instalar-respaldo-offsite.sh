#!/bin/bash
# Instala el BACKUP OFFSITE en ESTE equipo (el que RECIBE las copias; p.ej. el
# Dell). Crea un timer de usuario que cada día trae los respaldos del servidor
# (Lenovo) a esta máquina, para sobrevivir a una muerte del disco del servidor.
#
# Uso (en el equipo receptor, p.ej. el Dell):  bash scripts/instalar-respaldo-offsite.sh
#
# Requisito: este equipo debe poder entrar por SSH al servidor SIN contraseña
# (llave ya autorizada). Pruébalo antes con:
#   ssh -o BatchMode=yes samuel@192.168.100.47 'echo ok'
set -e
# respaldo-offsite.sh vive junto a este script (los dos en scripts/), asi que
# AQUI sigue siendo la carpeta de este script, no la raiz del repo.
AQUI="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/agenda-respaldo-offsite.service <<EOF
[Unit]
Description=Backup offsite: trae los respaldos del bot desde el servidor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $AQUI/respaldo-offsite.sh
EOF

cat > ~/.config/systemd/user/agenda-respaldo-offsite.timer <<EOF
[Unit]
Description=Trae el respaldo del bot a este equipo una vez al dia

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agenda-respaldo-offsite.timer
echo "Listo. Backup offsite instalado. Timer activo:"
systemctl --user list-timers agenda-respaldo-offsite.timer --no-pager
