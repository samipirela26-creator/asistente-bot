#!/usr/bin/env bash
# Backup OFFSITE: trae los respaldos del bot desde el SERVIDOR (Lenovo) a ESTE
# equipo (el Dell), para no perder la agenda si el disco del servidor muere.
# Un backup que vive en el mismo disco que el original NO es un backup.
#
# El archivo de respaldos/ ya es un snapshot CONSISTENTE: lo crea
# db.respaldo_diario() con la online backup API de SQLite (no es una copia
# cruda del .db vivo), así que traerlo por la red es seguro.
#
# Usa scp (viene con SSH; no hay que instalar rsync en el servidor). Para
# tan pocos archivos pequeños es más que suficiente.
#
# Config por entorno (con defaults para el caso Dell <- Lenovo):
#   RESPALDO_HOST     usuario@host del servidor
#   RESPALDO_RUTA     carpeta de respaldos en el servidor
#   RESPALDO_DESTINO  carpeta local donde guardar las copias
#
# Uso:  bash respaldo-offsite.sh
set -euo pipefail

HOST="${RESPALDO_HOST:-samuel@192.168.100.47}"
RUTA="${RESPALDO_RUTA:-/home/samuel/asistente/respaldos}"
DESTINO="${RESPALDO_DESTINO:-$HOME/respaldos-lenovo}"
mkdir -p "$DESTINO"

# scp sobre SSH:
#  - BatchMode=yes: nunca pide contraseña (si falta la llave, falla en vez de
#    colgarse esperando input).
#  - ConnectTimeout: si el servidor está apagado, corta rápido y el timer
#    reintentará en el próximo ciclo (no pasa nada por un día perdido).
#  - -p conserva fechas; el glob remoto lo expande el shell del servidor.
scp -p -o BatchMode=yes -o ConnectTimeout=10 \
    "$HOST:$RUTA/agenda-*.db" "$DESTINO/"

# Verifica que la copia MÁS RECIENTE no esté corrupta. Un backup que nunca
# compruebas no es un backup. Usa python3 (stdlib) para no depender del CLI
# de sqlite3, que puede no estar instalado.
ULTIMO="$(ls -1t "${DESTINO%/}"/agenda-*.db 2>/dev/null | head -1 || true)"
if [ -z "$ULTIMO" ]; then
  echo "AVISO: no se encontró ningún respaldo en $DESTINO" >&2
  exit 1
fi
python3 - "$ULTIMO" <<'PY'
import sqlite3, sys
ruta = sys.argv[1]
con = sqlite3.connect(ruta)
estado = con.execute("PRAGMA integrity_check").fetchone()[0]
con.close()
print(f"integridad de {ruta}: {estado}")
sys.exit(0 if estado == "ok" else 1)
PY
echo "Backup offsite OK: $(date -Iseconds) -> $DESTINO"
