#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de las apps de Samuel + salud del servidor.

Reutiliza la infraestructura del bot (Telegram, config y estado en BD), igual
que chequear_salud.py. No agrega dependencias: solo stdlib.

Usos (los corren timers de systemd):

    python3 monitor.py reporte    # resumen de TODAS las apps -> Telegram (5am y 5pm)
    python3 monitor.py vigilar    # avisa SOLO si una app se cayo (cada 15 min)

Y expone texto_apps() / reporte_texto() para que el comando /servidor del bot
responda en el momento (solo al administrador).

Cada app se considera CAIDA si su servicio systemd esta inactivo (cuando aplica)
o si su chequeo HTTP no responde. Primero las apps del Lenovo (servicio local),
luego la de Firebase (Airtek), como pidio el usuario.
"""

import sys
import logging
import subprocess
import urllib.request
import urllib.error
from html import escape as esc

import asistente as A
import db

log = logging.getLogger("agenda.monitor")

TIMEOUT = 6  # segundos por chequeo HTTP

# svc = servicio systemd --user (None si la app no vive en este servidor).
# url = chequeo HTTP. ok = codigos HTTP que cuentan como "viva".
APPS = [
    {"slug": "matrimonio", "nombre": "Matrimonio JS",
     "svc": "matrimonio", "url": "http://localhost:4000/", "ok": (200, 301, 302)},
    {"slug": "pizza", "nombre": "Pizza 4 Estaciones",
     "svc": "pizza", "url": "http://localhost:8080/", "ok": (200, 301, 302)},
    {"slug": "asistapp", "nombre": "AsistApp",
     "svc": "asistapp", "url": "http://localhost:3001/", "ok": (200, 301, 302)},
    {"slug": "airtek", "nombre": "Airtek (Firebase)",
     "svc": None, "url": "https://samipirela26-creator.github.io/airtek-evaluaciones/", "ok": (200,)},
]


def _svc_activo(svc):
    """True/False si el servicio esta activo; None si no se pudo determinar."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", svc],
                           capture_output=True, text=True, timeout=8)
        return r.stdout.strip() == "active"
    except Exception as e:  # pragma: no cover - entorno sin systemd
        log.warning("No pude consultar el servicio %s: %s", svc, e)
        return None


def _http_ok(url, ok_codes):
    """(ok, detalle) del chequeo HTTP. Cualquier fallo de red = caida."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "LarryMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            code = resp.status
            return (code in ok_codes), f"HTTP {code}"
    except urllib.error.HTTPError as e:
        return (e.code in ok_codes), f"HTTP {e.code}"
    except Exception as e:
        return False, f"sin respuesta ({type(e).__name__})"


def revisar():
    """Revisa todas las apps y devuelve una lista de dicts con su estado."""
    resultados = []
    for app in APPS:
        http_ok, http_det = _http_ok(app["url"], app["ok"])
        if app["svc"]:
            svc_ok = _svc_activo(app["svc"])
            if svc_ok is False:
                # Servicio muerto = caida clara, aunque el HTTP diera algo.
                ok = False
            elif svc_ok is None:
                # No se pudo saber el servicio: nos guiamos por el HTTP.
                ok = http_ok
            else:
                ok = http_ok
            estado_svc = ("activo" if svc_ok else
                          "inactivo" if svc_ok is False else "desconocido")
            detalle = f"servicio {estado_svc}, {http_det}"
        else:
            ok = http_ok
            detalle = http_det
        resultados.append({"slug": app["slug"], "nombre": app["nombre"],
                           "ok": ok, "detalle": detalle})
    return resultados


def texto_apps(resultados=None):
    """Bloque de texto (HTML de Telegram) con el estado de cada app."""
    r = resultados if resultados is not None else revisar()
    caidas = [a for a in r if not a["ok"]]
    if caidas:
        cabecera = f"🔴 <b>{len(caidas)} app(s) con problema</b>"
    else:
        cabecera = "🟢 <b>Todas las apps operativas</b>"
    lineas = [cabecera, ""]
    for a in r:
        emoji = "🟢" if a["ok"] else "🔴"
        lineas.append(f"{emoji} <b>{esc(a['nombre'])}</b> — {esc(a['detalle'])}")
    return "\n".join(lineas)


def reporte_texto():
    """Reporte completo: salud del servidor + estado de las apps."""
    try:
        import sistema
        maquina = sistema.estado_texto() + "\n\n"
    except Exception:
        maquina = ""
    return "📋 <b>Estado del servidor y las apps</b>\n\n" + maquina + texto_apps()


def _chat_ids(cfg):
    """Cuentas de Samuel a las que avisar (chat_ids + chat_id, sin placeholders)."""
    ids = []
    for cid in cfg.get("chat_ids", []) or []:
        cid = str(cid).strip()
        if cid and not cid.startswith("PEGA_") and cid not in ids:
            ids.append(cid)
    uno = str(cfg.get("chat_id", "")).strip()
    if uno and not uno.startswith("PEGA_") and uno not in ids:
        ids.append(uno)
    return ids


def _avisar(destinos, token, msg):
    for cid in destinos:
        try:
            A.enviar_mensaje(msg, token, cid)
        except Exception as e:
            log.warning("No pude avisar a %s: %s", cid, e)


def reporte():
    """Manda el reporte completo a las cuentas de Samuel (timer 5am/5pm)."""
    db.init_db()
    cfg, token, _ = A.cargar_config()
    destinos = _chat_ids(cfg)
    if not token or not destinos:
        log.error("Falta token o chat_id; no puedo enviar el reporte.")
        return
    _avisar(destinos, token, reporte_texto())
    log.info("Reporte diario enviado a %d cuenta(s).", len(destinos))


def vigilar():
    """Avisa (una sola vez) si una app se cayo, y de nuevo cuando se recupera."""
    db.init_db()
    cfg, token, _ = A.cargar_config()
    destinos = _chat_ids(cfg)
    if not token or not destinos:
        log.error("Falta token o chat_id; no puedo avisar de caidas.")
        return
    for a in revisar():
        clave = f"monitor_alert_{a['slug']}"
        ya_avisado = db.estado_get(clave) == "1"
        if not a["ok"] and not ya_avisado:
            _avisar(destinos, token,
                    f"🔴 <b>{esc(a['nombre'])} se cayó</b>\n{esc(a['detalle'])}\n"
                    f"Revisa el servicio en el servidor.")
            db.estado_set(clave, "1")
            log.warning("Caida detectada: %s (%s)", a["slug"], a["detalle"])
        elif a["ok"] and ya_avisado:
            _avisar(destinos, token, f"✅ <b>{esc(a['nombre'])} se recuperó.</b>")
            db.estado_set(clave, "0")
            log.info("Recuperada: %s", a["slug"])
        else:
            log.info("%s OK.", a["slug"]) if a["ok"] else None


def main():
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")
    modo = sys.argv[1] if len(sys.argv) > 1 else "reporte"
    if modo == "vigilar":
        vigilar()
    elif modo == "reporte":
        reporte()
    elif modo == "prueba":
        # Imprime el reporte por consola sin enviar nada (para probar en local).
        print(reporte_texto())
    else:
        print(f"Modo desconocido: {modo}. Usa: reporte | vigilar | prueba")


if __name__ == "__main__":
    main()
