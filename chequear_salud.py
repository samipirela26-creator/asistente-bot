#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Healthcheck del bot. Lo corre un timer de systemd cada pocos minutos.

El bot escribe un 'latido' (timestamp) en la BD en cada ciclo de getUpdates.
Si el latido lleva más de X minutos sin refrescarse, el bot está caído o
colgado: este script avisa por Telegram a las cuentas del dueño (una sola vez
por incidente) y vuelve a avisar cuando se recupera.

Nota: solo detecta "bot caído pero máquina viva" (el caso real de un crash).
Si la máquina entera está apagada, nadie puede avisar.

Uso:
    python3 chequear_salud.py            # umbral por defecto (10 min)
    python3 chequear_salud.py 15         # umbral en minutos
"""

import sys
import time
import logging

import asistente as A
import db

log = logging.getLogger("agenda.salud")

UMBRAL_MIN_DEFECTO = 10  # minutos sin latido = se considera caído


def main():
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")
    umbral_min = UMBRAL_MIN_DEFECTO
    if len(sys.argv) > 1:
        try:
            umbral_min = float(sys.argv[1])
        except ValueError:
            pass
    umbral = umbral_min * 60

    db.init_db()
    cfg, token, _ = A.cargar_config()
    chat_ids = []
    for cid in cfg.get("chat_ids", []) or []:
        cid = str(cid).strip()
        if cid and not cid.startswith("PEGA_") and cid not in chat_ids:
            chat_ids.append(cid)
    uno = str(cfg.get("chat_id", "")).strip()
    if uno and uno not in chat_ids:
        chat_ids.append(uno)
    if not token or not chat_ids:
        log.error("Falta token o chat_id; no puedo avisar.")
        return

    latido = db.estado_get("latido")
    ahora = time.time()
    if not latido:
        log.info("Aún no hay latido (bot recién instalado). Nada que reportar.")
        return
    atraso = ahora - float(latido)
    caido = atraso > umbral
    ya_avisado = db.estado_get("salud_alertada") == "1"

    if caido and not ya_avisado:
        mins = int(atraso / 60)
        msg = (f"🛑 <b>El bot no responde</b>\nLleva ~{mins} min sin dar señales "
               f"(último latido hace {mins} min). Revisa el servicio.")
        for cid in chat_ids:
            try:
                A.enviar_mensaje(msg, token, cid)
            except Exception as e:
                log.warning("No pude avisar a %s: %s", cid, e)
        db.estado_set("salud_alertada", "1")
        log.warning("Bot caído: atraso %.0fs", atraso)
    elif not caido and ya_avisado:
        for cid in chat_ids:
            try:
                A.enviar_mensaje("✅ <b>El bot se recuperó</b> y vuelve a responder.",
                                 token, cid)
            except Exception as e:
                log.warning("No pude avisar recuperación a %s: %s", cid, e)
        db.estado_set("salud_alertada", "0")
        log.info("Bot recuperado.")
    else:
        log.info("Bot OK (último latido hace %.0fs).", atraso)


if __name__ == "__main__":
    main()
