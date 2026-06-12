#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Asistente personal por Telegram.
- Te envia un RESUMEN DIARIO con tus tareas y eventos.
- Te avisa de RECORDATORIOS proximos.

No necesita instalar nada: usa solo Python estandar.

Uso:
    python asistente.py resumen      -> envia el resumen del dia
    python asistente.py recordatorios-> envia solo los recordatorios proximos
    python asistente.py chatid       -> te ayuda a encontrar tu chat_id
    python asistente.py prueba       -> envia un mensaje de prueba
"""

import json
import sys
import os
import time
import logging
import datetime
import urllib.error
import urllib.parse
import urllib.request
from html import escape as esc

log = logging.getLogger("agenda.asistente")
LIMITE_TELEGRAM = 4096  # tope de caracteres por mensaje en la API de Telegram

# ---- Rutas de los archivos (estan junto a este script) ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TAREAS_PATH = os.path.join(BASE_DIR, "tareas.json")

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def cargar_json(ruta, por_defecto):
    if not os.path.exists(ruta):
        return por_defecto
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


# Secretos que se pueden dar por variable de entorno (tienen prioridad sobre
# config.json). Asi rotar/cambiar un token manana es trivial y sin tocar el
# archivo: basta exportar AGENDA_TOKEN=... antes de arrancar el bot.
_CLAVES_SECRETAS = (
    "token", "gemini_api_key", "groq_api_key", "openrouter_api_key",
    "mistral_api_key", "zhipu_api_key", "xai_api_key",
)


def _proteger_config():
    """Asegura permisos 600 en config.json (solo el dueno lo lee/escribe).
    Silencioso: si no existe o el FS no lo permite (p.ej. clon de solo lectura),
    no falla."""
    try:
        if os.path.exists(CONFIG_PATH):
            os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def cargar_config():
    cfg = cargar_json(CONFIG_PATH, {})
    _proteger_config()
    # Las variables de entorno PISAN al config.json (cambio facil de tokens).
    for clave in _CLAVES_SECRETAS:
        env = os.environ.get("AGENDA_" + clave.upper(), "").strip()
        if env:
            cfg[clave] = env
    token = cfg.get("token", "").strip()
    chat_id = str(cfg.get("chat_id", "")).strip()
    return cfg, token, chat_id


def todos_los_chats(cfg):
    """Todas las cuentas configuradas (chat_ids lista + chat_id viejo)."""
    ids = []
    for cid in cfg.get("chat_ids", []) or []:
        cid = str(cid).strip()
        if cid and not cid.startswith("PEGA_") and cid not in ids:
            ids.append(cid)
    uno = str(cfg.get("chat_id", "")).strip()
    if uno and uno not in ids:
        ids.append(uno)
    return ids


def api_telegram(metodo, params, token, reintentos=3):
    """Llama a la API de Telegram. Devuelve el JSON de respuesta.
    Ante un 429 (Too Many Requests) espera 'retry_after' y reintenta."""
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    datos = urllib.parse.urlencode(params).encode("utf-8")
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(url, data=datos, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < reintentos - 1:
                try:
                    cuerpo = json.loads(e.read().decode("utf-8"))
                    espera = cuerpo.get("parameters", {}).get("retry_after", 1)
                except Exception:
                    espera = 1
                time.sleep(espera + max(0.1, espera * 0.1))  # +10% de margen
                continue
            raise
    return {"ok": False, "description": "agotados los reintentos"}


def _trocear(texto, limite=LIMITE_TELEGRAM):
    """Parte un texto largo en trozos <= limite sin cortar a media linea."""
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite:
            if actual:
                partes.append(actual)
            # una sola linea mas larga que el limite: cortar duro
            while len(linea) > limite:
                partes.append(linea[:limite])
                linea = linea[limite:]
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes


def enviar_mensaje(texto, token, chat_id, botones=None):
    """botones: lista de filas de botones inline, ej:
    [[{"text": "✅ Hecho", "callback_data": "rec_done:5"}]]
    Si el texto supera 4096 chars se envia en varios mensajes; los botones
    solo van en el ultimo trozo."""
    trozos = _trocear(texto)
    res = None
    for i, trozo in enumerate(trozos):
        params = {
            "chat_id": chat_id,
            "text": trozo,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if botones and i == len(trozos) - 1:
            params["reply_markup"] = json.dumps({"inline_keyboard": botones})
        res = api_telegram("sendMessage", params, token)
        if not res.get("ok"):
            log.error("Telegram rechazo el envio: %s", res)
    return res


def fecha_legible(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def parse_fecha(texto):
    """Acepta 'YYYY-MM-DD'. Devuelve date o None."""
    try:
        return datetime.datetime.strptime(texto.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def construir_resumen(tareas, hoy):
    eventos = tareas.get("eventos", [])
    pendientes = tareas.get("pendientes", [])

    hoy_eventos = []
    proximos = []
    for ev in eventos:
        f = parse_fecha(ev.get("fecha", ""))
        if f is None:
            continue
        dias = (f - hoy).days
        if dias == 0:
            hoy_eventos.append(ev)
        elif 0 < dias <= 7:
            proximos.append((dias, ev))
    proximos.sort(key=lambda x: x[0])

    lineas = []
    lineas.append(f"☀️ <b>¡Buenos días!</b>\n📆 {fecha_legible(hoy)}")
    lineas.append("")

    if hoy_eventos:
        lineas.append("📅 <b>Hoy:</b>")
        for ev in hoy_eventos:
            hora = ev.get("hora", "")
            prefijo = f"🕐 {esc(hora)} · " if hora else "• "
            lineas.append(f"  {prefijo}{esc(ev.get('titulo', ''))}")
    else:
        lineas.append("📅 <b>Hoy:</b> sin eventos agendados ✨")
    lineas.append("")

    if pendientes:
        lineas.append("📝 <b>Pendientes:</b>")
        for p in pendientes:
            lineas.append(f"  • {esc(p)}")
        lineas.append("")

    if proximos:
        lineas.append("🔜 <b>Esta semana:</b>")
        for dias, ev in proximos:
            cuando = "mañana" if dias == 1 else f"en {dias} días"
            lineas.append(f"  • {esc(ev.get('titulo', ''))} ({cuando})")
        lineas.append("")

    lineas.append("💪 ¡Que tengas un gran día!")
    return "\n".join(lineas)


def construir_recordatorios(tareas, hoy):
    """Eventos de hoy y manana, pensado para un aviso corto."""
    eventos = tareas.get("eventos", [])
    avisos = []
    for ev in eventos:
        f = parse_fecha(ev.get("fecha", ""))
        if f is None:
            continue
        dias = (f - hoy).days
        if dias in (0, 1):
            cuando = "HOY" if dias == 0 else "MAÑANA"
            hora = ev.get("hora", "")
            extra = f" a las {esc(hora)}" if hora else ""
            avisos.append(f"- {cuando}{extra}: {esc(ev.get('titulo', ''))}")
    if not avisos:
        return None
    return "<b>Recordatorio</b>\n" + "\n".join(avisos)


def comando_chatid(token):
    print("Abre Telegram, busca tu bot y envíale cualquier mensaje (por ej. 'hola').")
    print("Luego vuelve aquí y pulsa Enter...")
    input()
    res = api_telegram("getUpdates", {}, token)
    if not res.get("ok") or not res.get("result"):
        print("No recibi mensajes. Asegurate de haberle escrito al bot y reintenta.")
        return
    for upd in res["result"]:
        msg = upd.get("message") or upd.get("edited_message")
        if msg:
            chat = msg["chat"]
            print(f"Tu chat_id es: {chat['id']}  (nombre: {chat.get('first_name','')})")
    print("\nCopia ese numero en config.json -> \"chat_id\".")


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if len(sys.argv) < 2:
        print(__doc__)
        return

    accion = sys.argv[1].lower()
    cfg, token, chat_id = cargar_config()

    if not token:
        print("Falta el token. Edita config.json y pega el token de @BotFather.")
        return

    if accion == "chatid":
        comando_chatid(token)
        return

    if not chat_id:
        print("Falta el chat_id. Ejecuta:  python asistente.py chatid")
        return

    if accion == "prueba":
        enviar_mensaje("Hola! Tu asistente esta conectado correctamente.", token, chat_id)
        print("Mensaje de prueba enviado.")
        return

    try:
        import db
        db.init_db()
        tareas = db.cargar_tareas()
    except Exception:
        tareas = cargar_json(TAREAS_PATH, {"eventos": [], "pendientes": []})
    hoy = datetime.date.today()

    destinos = todos_los_chats(cfg) or [chat_id]
    if accion == "resumen":
        msg = None
        api_key = cfg.get("gemini_api_key", "").strip()
        if api_key:
            try:
                import db as _db
                import gemini_ia
                msg = gemini_ia.redactar_resumen(
                    tareas, _db.cargar_proyectos(solo_pendientes=True),
                    api_key, racha=_db.racha(), lecturas=_db.get_lecturas(),
                    cfg=cfg)
            except Exception as e:
                log.warning("Resumen con IA fallo, uso el clasico: %s", e)
        if not msg:
            msg = construir_resumen(tareas, hoy)
        for cid in destinos:
            enviar_mensaje(msg, token, cid)
        print(f"Resumen enviado a {len(destinos)} cuenta(s).")
    elif accion == "noche":
        import db as _db
        hechos = _db.actividad_de()
        pend = tareas.get("pendientes", [])
        lineas = ["🌙 <b>Resumen del dia</b>"]
        if hechos:
            lineas.append(f"✅ Hoy completaste <b>{len(hechos)}</b> cosa(s):")
            lineas += [f"  • {h['texto'] or h['tipo']}" for h in hechos[:8]]
        else:
            lineas.append("Hoy no registraste avances; mañana será mejor día 🌱")
        r = _db.racha()
        if r > 1:
            lineas.append(f"🔥 Racha: {r} días seguidos avanzando ✊")
        if pend:
            lineas.append(f"\n📝 Quedan {len(pend)} pendiente(s) para mañana.")
        lineas.append("😴 Descansa bien!")
        msg = "\n".join(lineas)
        for cid in destinos:
            enviar_mensaje(msg, token, cid)
        print("Resumen nocturno enviado.")
    elif accion == "recordatorios":
        texto = construir_recordatorios(tareas, hoy)
        if texto:
            for cid in destinos:
                enviar_mensaje(texto, token, cid)
            print("Recordatorios enviados.")
        else:
            print("No hay recordatorios para hoy/mañana.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
