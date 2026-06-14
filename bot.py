#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot interactivo de Telegram para tu agenda.

Lo dejas corriendo (python bot.py) y le escribes.
Mejoras incluidas:
  - Base de datos SQLite (db.py): robusto, no se corrompe.
  - Recordatorios con hora y repeticion (te avisa solo a la hora exacta).
  - IA con Gemini para frases libres.

Comandos rapidos (sin IA): lista, agrega, borra, evento, resumen, ayuda.
Para frases libres y recordatorios con hora, usa lenguaje natural y responde la IA.

Apagar: Ctrl + C.
"""

import os
import re
import time
import json
import signal
import logging
import threading
import datetime
import urllib.parse
import urllib.request
import urllib.error
from html import escape as esc

try:
    import fcntl  # lock anti-doble-instancia (solo POSIX; en runtime es Linux)
except ImportError:
    fcntl = None

log = logging.getLogger("agenda.bot")

import asistente as A
import db
import fechas
import busqueda
import sistema

try:
    import gemini_ia
except ImportError:
    gemini_ia = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_LOCK = None  # descriptor del lock anti-doble-instancia (se conserva abierto)

# Sube este numero cada vez que cambies el bot y escribe que cambio en NOVEDADES.
# Al arrancar, si la version es nueva, el bot te avisa por Telegram una sola vez.
VERSION = "3.2"
NOVEDADES = (
    "<b>Parte de novedades — versión 3.2</b>\n\n"
    "Me permito informarle de las mejoras incorporadas a su servicio:\n"
    "• <b>Su nombre:</b> tendré el honor de preguntarle cómo desea que me "
    "dirija a usted, y en adelante alternaré entre su nombre y un sobrio "
    "«señor», según convenga.\n"
    "• <b>Cambiar el trato:</b> en el menú dispone del botón <b>✏️ Mi "
    "nombre</b> para indicarme un nombre distinto cuando lo desee.\n"
    "• <b>Estado para todas sus cuentas:</b> el comando «estado» queda "
    "disponible desde cualquiera de sus cuentas personales, no solo la "
    "primera.\n\n"
    "Quedo, como siempre, a su entera disposición."
)


def notificar_actualizacion(token, chat_ids):
    """Si la VERSION cambió desde el ultimo aviso, manda las NOVEDADES a todas
    las cuentas una sola vez (guarda la version avisada en la BD)."""
    if db.estado_get("version_avisada") == VERSION:
        return
    for cid in chat_ids:
        try:
            A.enviar_mensaje(NOVEDADES, token, cid)
        except Exception as e:
            log.warning("No pude enviar aviso de actualizacion a %s: %s", cid, e)
    db.estado_set("version_avisada", VERSION)


def chat_ids_permitidos(cfg):
    """Cuentas personales del dueño (las del config). Comparten un mismo
    espacio de datos: el dueño 'principal'. Acepta 'chat_ids' (lista) y/o
    el viejo 'chat_id' (uno solo)."""
    ids = []
    for cid in cfg.get("chat_ids", []) or []:
        cid = str(cid).strip()
        if cid and not cid.startswith("PEGA_") and cid not in ids:
            ids.append(cid)
    uno = str(cfg.get("chat_id", "")).strip()
    if uno and uno not in ids:
        ids.append(uno)
    return ids


def dueno_de(emisor, cfg):
    """A qué espacio de datos pertenece quien escribe.
    Tus cuentas personales -> 'principal' (comparten tus datos de siempre).
    Cualquier otro usuario -> su propio chat_id (datos aislados)."""
    return db.DUENO_PRINCIPAL if str(emisor) in chat_ids_permitidos(cfg) else str(emisor)


# --- Rate-limit por usuario -------------------------------------------------
# El bot está ABIERTO (sin lista blanca), así que un usuario o un script podría
# inundarlo de mensajes y gastar cuota de IA o tumbar el servicio. Limitamos por
# chat_id con una ventana deslizante en memoria (stdlib, sin dependencias).
RL_VENTANA = 60        # segundos de la ventana
RL_MAXIMO = 15         # mensajes permitidos por usuario en esa ventana
_rl_marcas = {}        # chat_id -> lista de timestamps recientes
_rl_avisado = {}       # chat_id -> ts del último aviso "vas muy rápido"
_rl_lock = threading.Lock()


def permitido(emisor):
    """True si el usuario puede procesar otro mensaje ahora.
    Devuelve (ok, avisar): 'avisar' es True solo la primera vez que se pasa en
    una ventana, para mandarle UN aviso y no spamear de vuelta."""
    ahora = time.time()
    emisor = str(emisor)
    with _rl_lock:
        marcas = [t for t in _rl_marcas.get(emisor, ()) if ahora - t < RL_VENTANA]
        if len(marcas) >= RL_MAXIMO:
            _rl_marcas[emisor] = marcas  # purga las viejas
            avisar = (ahora - _rl_avisado.get(emisor, 0)) > RL_VENTANA
            if avisar:
                _rl_avisado[emisor] = ahora
            return False, avisar
        marcas.append(ahora)
        _rl_marcas[emisor] = marcas
        return True, False


def destinos_de(dueno, cfg):
    """A qué chats hay que enviarle algo a un dueño (ej. sus recordatorios).
    'principal' -> todas tus cuentas; otro usuario -> solo su chat."""
    if dueno == db.DUENO_PRINCIPAL:
        return chat_ids_permitidos(cfg)
    return [dueno]


def creador(cfg):
    """El chat del USUARIO CREADOR (tú): los avisos técnicos (novedades de
    versión, alertas de la máquina) van SOLO aquí, no a otras cuentas ni a otros
    usuarios. Usa 'chat_id_creador' del config si existe; si no, la primera
    cuenta configurada. Devuelve [] si no hay ninguna."""
    explicito = str(cfg.get("chat_id_creador", "")).strip()
    if explicito and not explicito.startswith("PEGA_"):
        return [explicito]
    permitidos = chat_ids_permitidos(cfg)
    return permitidos[:1]


def es_admin(chat_id, cfg):
    """¿Quien escribe es el dueño (administrador)? TODAS tus cuentas personales
    del config lo son, no solo la primera. Distinto de 'creador', que es UNA
    sola cuenta destino para las alertas tecnicas (para no duplicarlas)."""
    return str(chat_id) in [str(c) for c in chat_ids_permitidos(cfg)]


# --- Horas de silencio ------------------------------------------------------
# Nada de recordatorios de madrugada: si uno vence en la franja de silencio, se
# guarda y se entrega cuando termina (por defecto 23:00 -> 07:00). Configurable
# con 'silencio_inicio'/'silencio_fin' en config.json (horas 0-23).
SILENCIO_INICIO = 23
SILENCIO_FIN = 7


def _franja_silencio(cfg):
    ini = int(cfg.get("silencio_inicio", SILENCIO_INICIO))
    fin = int(cfg.get("silencio_fin", SILENCIO_FIN))
    return ini, fin


# ------------------------------------------------------------------ utilidades
def texto_proyectos(completo=False):
    """Resumen de proyectos grandes con su progreso y fase actual."""
    proyectos = db.cargar_proyectos(solo_pendientes=False)
    if not proyectos:
        return "🏗 No tienes proyectos. Crea uno: <i>crea el proyecto X con fases: a, b, c</i>"
    out = ["🏗 <b>Proyectos grandes</b>"]
    for p in proyectos:
        pendientes = [f for f in p["fases"] if not f["hecho"]]
        prog = db.progreso_proyecto(p["nombre"])
        barra = f" ({prog[0]}/{prog[1]})" if prog else ""
        if not pendientes:
            out.append(f"\n✅ <b>{esc(p['nombre'])}</b>{barra} — terminado 🎉")
            continue
        out.append(f"\n📌 <b>{esc(p['nombre'])}</b>{barra}")
        if completo:
            for f in p["fases"]:
                marca = "✅" if f["hecho"] else "▫️"
                mins = f" ~{f['minutos']}min" if f.get("minutos") else ""
                out.append(f"  {marca} {f['orden']}. {esc(f['titulo'])}{mins}")
        else:
            f = pendientes[0]
            mins = f" (~{f['minutos']} min)" if f.get("minutos") else ""
            out.append(f"  ▶️ Sigue: <i>{esc(f['titulo'])}</i>{mins}")
    return "\n".join(out)


def texto_fases(nombre):
    """Todas las fases de UN proyecto (busca por nombre parcial)."""
    obj = nombre.strip().lower()
    palabras = [w for w in re.split(r"\W+", obj)
                if w and w not in ("de", "del", "la", "el", "los", "las", "que")]
    for p in db.cargar_proyectos(solo_pendientes=False):
        nom = p["nombre"].lower()
        if obj in nom or (palabras and all(w in nom for w in palabras)):
            prog = db.progreso_proyecto(p["nombre"])
            barra = f" ({prog[0]}/{prog[1]})" if prog else ""
            out = [f"📌 <b>{esc(p['nombre'])}</b>{barra}"]
            for f in p["fases"]:
                marca = "✅" if f["hecho"] else "▫️"
                mins = f" ~{f['minutos']}min" if f.get("minutos") else ""
                out.append(f"  {marca} {f['orden']}. {esc(f['titulo'])}{mins}")
            return "\n".join(out)
    return None


def texto_uso():
    filas = db.uso_resumen(7)
    total = sum(n for _, n in filas)
    cuerpo = "\n".join(f"  {'📍' if i == 0 else '▫️'} {f}: {n} llamadas"
                       for i, (f, n) in enumerate(filas))
    msg = (f"🤖 <b>Uso de la IA</b> (límite gratis ≈ 250/día)\n{cuerpo}\n"
           f"  Σ últimos 7 días: <b>{total}</b>")
    pausa = float(db.estado_get("ia_pausada_hasta", 0) or 0)
    if time.time() < pausa:
        mins = int((pausa - time.time()) / 60) + 1
        msg += f"\n  😴 IA en pausa por cuota; vuelve en ~{mins} min (respaldos activos)"
    else:
        msg += "\n  ✅ IA activa · respaldos: OpenRouter, Mistral, GLM, xAI"
    return msg


def botones_proyectos():
    """Una fila de botones por proyecto con fases pendientes + ver todas."""
    filas = []
    for p in db.cargar_proyectos(solo_pendientes=True):
        if not p["fases"]:
            continue
        nom = p["nombre"][:14]
        filas.append([
            {"text": f"✅ Hecha · {nom}", "callback_data": f"proy_done:{p['id']}"},
            {"text": "▶️ Sig.", "callback_data": f"proy_next:{p['id']}"},
        ])
    filas.append([{"text": "📋 Ver todas las fases", "callback_data": "proy_all:0"}])
    return filas


def botones_menu():
    return [
        [{"text": "🏗 Proyectos", "callback_data": "menu:proyectos"},
         {"text": "📋 Lista", "callback_data": "menu:lista"}],
        [{"text": "☀️ Resumen", "callback_data": "menu:resumen"},
         {"text": "💡 Sugiéreme algo", "callback_data": "menu:sugerencia"}],
        [{"text": "🗒 Notas", "callback_data": "menu:notas"},
         {"text": "⏰ Recordatorios", "callback_data": "menu:recordatorios"}],
        [{"text": "🤖 Uso de IA", "callback_data": "menu:uso"},
         {"text": "🎯 Intereses", "callback_data": "menu:intereses"}],
        [{"text": "📖 Lecturas", "callback_data": "menu:lecturas"},
         {"text": "❓ Ayuda", "callback_data": "menu:ayuda"}],
        [{"text": "✏️ Mi nombre", "callback_data": "menu:nombre"}],
    ]


def texto_intereses():
    ints = db.get_intereses()
    if not ints:
        return ("🎯 No tienes intereses guardados.\n"
                "Dime: <i>me interesa mejorar en guitarra</i>")
    return "🎯 <b>Tus intereses</b>\n" + "\n".join(
        f"  {i}. {esc(t)}" for i, t in enumerate(ints, 1))


def texto_lista(tareas):
    pend = tareas.get("pendientes", [])
    ev = tareas.get("eventos", [])
    rec = db.listar_recordatorios()
    out = []
    proyectos = [p for p in db.cargar_proyectos(solo_pendientes=True) if p["fases"]]
    if proyectos:
        out.append("🏗 <b>Proyectos grandes</b> (escribe <b>proyectos</b> para los botones)")
        for p in proyectos:
            prog = db.progreso_proyecto(p["nombre"])
            barra = f" ({prog[0]}/{prog[1]})" if prog else ""
            out.append(f"  📌 {esc(p['nombre'])}{barra} → <i>{esc(p['fases'][0]['titulo'])}</i>")
        out.append("")
    out.append("📝 <b>Pendientes rapidos</b>")
    if pend:
        out += [f"  {i}. {esc(p)}" for i, p in enumerate(pend, 1)]
    else:
        out.append("  ✨ nada pendiente")
    out.append("")
    out.append("📅 <b>Eventos</b>")
    if ev:
        for i, e in enumerate(ev, 1):
            hora = f" · {esc(e['hora'])}" if e.get("hora") else ""
            out.append(f"  {i}. {esc(e.get('titulo',''))}\n      🗓 {esc(e.get('fecha',''))}{hora}")
    else:
        out.append("  ✨ ninguno agendado")
    out.append("")
    out.append("⏰ <b>Recordatorios</b>")
    if rec:
        for i, r in enumerate(rec, 1):
            rep = f" 🔁 {esc(r['repetir'])}" if r["repetir"] else ""
            ins = " 🔔 insistente" if r.get("insistir_min") else ""
            cuando = esc(r["cuando"].replace("T", " · "))
            out.append(f"  {i}. {esc(r['texto'])}\n      🕐 {cuando}{rep}{ins}")
    else:
        out.append("  ✨ ninguno")
    return "\n".join(out)


def quitar_pendiente(tareas, objetivo):
    pend = tareas.get("pendientes", [])
    objetivo = objetivo.strip()
    if objetivo.isdigit():
        idx = int(objetivo) - 1
        return pend.pop(idx) if 0 <= idx < len(pend) else None
    obj = objetivo.lower()
    for i, p in enumerate(pend):
        if obj in p.lower():
            return pend.pop(i)
    return None


def quitar_evento(tareas, objetivo):
    ev = tareas.get("eventos", [])
    objetivo = objetivo.strip()
    if objetivo.isdigit():
        idx = int(objetivo) - 1
        return ev.pop(idx) if 0 <= idx < len(ev) else None
    obj = objetivo.lower()
    for i, e in enumerate(ev):
        if obj in e.get("titulo", "").lower():
            return ev.pop(i)
    return None


# ------------------------------------------------------ atajos instantaneos
def atajo(texto, tareas):
    """Atajos exactos que no necesitan IA (instantaneos, gratis).
    Devuelve (respuesta, hubo_cambios) o None si no es un atajo."""
    low = texto.strip().lower()

    if low in ("/start", "ayuda", "/ayuda", "help"):
        return (
            "👋 <b>Hola! Soy tu agenda con IA.</b>\n"
            "Escribeme con naturalidad, por ejemplo:\n\n"
            "⏰ <i>recuérdame llamar al banco mañana a las 10</i>\n"
            "🏗 <i>crea el proyecto pizzeria cuatro estaciones</i>\n"
            "▶️ <i>cual es la siguiente fase de la pizzeria?</i>\n"
            "💡 <i>estoy esperando, tengo internet y 15 min</i>\n"
            "✅ <i>ya pague la luz, quitalo</i>\n"
            "🗒 <i>anota idea para el horno</i>\n"
            "📖 <i>quede en Juan 5</i>\n\n"
            "Atajos: <b>menu</b> · <b>lista</b> · <b>proyectos</b> · <b>resumen</b> · <b>notas</b>",
            False,
        )

    if low in ("lista", "/lista", "tareas", "mis tareas"):
        return texto_lista(tareas), False

    if low in ("resumen", "/resumen"):
        return A.construir_resumen(tareas, datetime.date.today()), False

    m = re.match(r"^(?:anota|nota)[:\s]+(.+)$", texto.strip(), re.I)
    if m:
        db.add_nota(m.group(1).strip())
        return f"🗒 Nota guardada:\n<i>{esc(m.group(1).strip())}</i>", False

    if low in ("notas", "/notas", "mis notas"):
        notas = db.buscar_notas()
        if notas:
            return "🗒 <b>Tus notas</b>\n" + "\n".join(
                f"  {i}. {esc(n['texto'])}\n      📆 {n['fecha'][:10]}"
                for i, n in enumerate(notas, 1)), False
        return "🗒 No tienes notas todavia.\nGuarda una con: <i>anota tu idea</i>", False

    if low in ("uso", "/uso", "tokens", "cuota") or \
            re.search(r"\btokens?\b", low) or \
            re.search(r"(como|cómo)\s+vamos\s+con\s+(la\s+|el\s+)?(ia|uso|cuota)", low):
        return texto_uso(), False

    if low in ("fases", "/fases", "mis fases", "todas las fases"):
        return texto_proyectos(completo=True), False


    if low in ("recordatorios", "/recordatorios", "mis recordatorios"):
        rec = db.listar_recordatorios()
        if rec:
            return "⏰ <b>Tus recordatorios</b>\n" + "\n".join(
                f"  {i}. {esc(r['texto'])}\n      🕐 {esc(r['cuando'].replace('T',' · '))}"
                for i, r in enumerate(rec, 1)), False
        return "⏰ No tienes recordatorios. ✨", False

    return None


# ------------------------- parser por reglas (gratis, sin gastar cuota de IA)
def procesar_simple(texto, tareas, estricto=False):
    """Parser basico por reglas.
    Con estricto=True devuelve None si no entiende (para que decida la IA).
    Con estricto=False responde siempre (respaldo cuando la IA no esta)."""
    t = texto.strip()
    low = t.lower()

    m = re.match(r"^(borra|elimina|quita)\s+evento\s+(.+)$", low)
    if m:
        q = quitar_evento(tareas, t[m.start(2):])
        return (f"🗑 Listo, eliminé el evento: <i>{esc(q.get('titulo',''))}</i>", True) if q \
            else ("🤔 No encontré ese evento. Escribe <b>lista</b>.", False)

    m = re.match(r"^(borra|elimina|quita)\s+(?:pendiente\s+|tarea\s+)?(.+)$", low)
    if m:
        q = quitar_pendiente(tareas, t[m.start(2):])
        return (f"✅ Listo, borré: <i>{esc(q)}</i>", True) if q \
            else ("🤔 No encontré ese pendiente. Escribe <b>lista</b>.", False)

    m = re.match(r"^evento\s+(\d{4}-\d{2}-\d{2})\s+(?:(\d{1,2}:\d{2})\s+)?(.+)$", t, re.I)
    if m:
        fecha, hora, titulo = m.group(1), m.group(2), m.group(3)
        nuevo = {"fecha": fecha, "titulo": titulo.strip()}
        if hora:
            nuevo["hora"] = hora
        tareas.setdefault("eventos", []).append(nuevo)
        return f"📅 Agendado: <i>{esc(titulo.strip())}</i>\n      🗓 {fecha}" + (f" · {hora}" if hora else ""), True

    m = re.match(r"^(agrega|añade|anade|nuevo|nueva)\s+(?:pendiente\s+|tarea\s+)?(.+)$", t, re.I)
    if m:
        nuevo = m.group(2).strip()
        tareas.setdefault("pendientes", []).append(nuevo)
        return f"📝 Agregado a pendientes: <i>{esc(nuevo)}</i>", True

    # "recuerdame X mañana a las 10" -> recordatorio local, sin gastar IA
    r = fechas.parsear(t)
    if r:
        cuando, rep, txt = r
        # fechas.parsear solo devuelve algo cuando el usuario dio una hora real
        # (si no, devuelve None y decide la IA): la hora es explícita, así que
        # suena a su hora aunque sea de madrugada (no se difiere).
        db.add_recordatorio(cuando, txt, rep, hora_explicita=True)
        extra = f"\n      🔁 se repite {esc(rep)}" if rep else ""
        return (f"⏰ Recordatorio: <i>{esc(txt)}</i>\n"
                f"      🕐 {esc(cuando.replace('T', ' · '))}{extra}", False)

    # "agendame dentista mañana 10am" -> evento local, sin IA
    ev = fechas.parsear_evento(t)
    if ev:
        fecha, hora, titulo = ev
        nuevo = {"fecha": fecha, "titulo": titulo}
        if hora:
            nuevo["hora"] = hora
        tareas.setdefault("eventos", []).append(nuevo)
        return (f"📅 Agendado: <i>{esc(titulo)}</i>\n      🗓 {fecha}"
                + (f" · {hora}" if hora else ""), True)

    # "complete la fase de X" -> sin IA (solo si el proyecto existe)
    m = re.match(r"^(?:complete|completé|termine|terminé|ya\s+hice)\s+"
                 r"(?:la\s+)?fase\s+(?:de\s+|del\s+|de\s+la\s+)?(.+)$", low)
    if m:
        proy = m.group(1).strip(" ?!.")
        comp, sig = db.completar_fase(proy)
        if comp:
            db.log_actividad("fase", comp["titulo"])
            msg = f"✅ <b>Fase completada:</b> {esc(comp['titulo'])}"
            prog = db.progreso_proyecto(proy)
            if prog:
                msg += f"\n      📊 Vas {prog[0]} de {prog[1]} ✊"
            r2 = db.racha()
            if r2 > 1:
                msg += f"\n      🔥 Racha: {r2} días seguidos!"
            msg += (f"\n      ▶️ Sigue: <i>{esc(sig['titulo'])}</i>" if sig
                    else "\n      🎉 <b>Proyecto terminado!</b>")
            return (msg, False)

    # "fases de X" / "muestrame las fases del proyecto X" -> sin IA
    m = re.match(r"^(?:muestrame\s+|muéstrame\s+|dime\s+|ver\s+|"
                 r"(?:que|qué|cuales|cuáles)\s+(?:son\s+)?)?"
                 r"(?:las\s+)?fases\s+(?:que\s+)?(?:tiene\s+|tengo\s+(?:en|de)\s+)?"
                 r"(?:de\s+|del\s+|de\s+la\s+|en\s+)?(?:proyecto\s+)?(.+)$", low)
    if m:
        txt = texto_fases(m.group(1).strip(" ?!."))
        if txt:
            return (txt, False)

    # "que sigue en X" / "siguiente fase de X" -> sin IA
    m = re.match(r"^(?:que|qué|cual|cuál)?\s*(?:es\s+la\s+)?(?:sigue|siguiente\s+fase)"
                 r"\s*(?:en|de|del|de\s+la|con)?\s+(.+)$", low)
    if m:
        proy = m.group(1).strip(" ?!.")
        f = db.fase_actual(proy)
        if f:
            ctx = f"\n      🏷 {esc(f['contexto'])}" if f["contexto"] else ""
            mins = f" (~{f['minutos']} min)" if f["minutos"] else ""
            return (f"▶️ Siguiente fase:\n      <b>{esc(f['titulo'])}</b>{mins}{ctx}", False)

    # "por donde voy/iba" -> lecturas, sin IA
    if re.match(r"^por\s+d[oó]nde\s+(voy|iba|quede|quedé)\b", low) or \
            low in ("lecturas", "/lecturas", "mis lecturas"):
        lect = db.get_lecturas()
        if lect:
            return ("📖 <b>Lecturas</b>\n" + "\n".join(
                f"  • <b>{esc(l['nombre'])}</b>: {esc(l['marcador'])}\n      📆 {l['actualizado']}"
                for l in lect), False)
        return ("📖 No tienes lecturas registradas. Dime: <i>quede en Juan 5</i>", False)

    # "ya lo hice / ya pague X / listo X" -> apagar recordatorio insistente
    m = re.match(r"^(ya\s+(?:lo\s+hice|esta|estuvo)|listo|hecho)\b\s*(.*)$", low)
    if m:
        objetivo = m.group(2).strip()
        rec = db.listar_recordatorios()
        insistentes = [r for r in rec if r.get("insistir_min")]
        if len(insistentes) == 1 and not objetivo:
            q = db.borrar_recordatorio(insistentes[0]["texto"])
            return (f"🎉 Bien hecho! Apagué el recordatorio: <i>{esc(q['texto'])}</i>", False)
        if objetivo:
            q = db.borrar_recordatorio(objetivo)
            if q:
                return (f"🎉 Bien hecho! Apagué el recordatorio: <i>{esc(q['texto'])}</i>", False)

    # "pausa / yo te aviso / deja de recordarme / silencia" -> callar la
    # insistencia automatica AL INSTANTE. Antes la IA solo respondía "de acuerdo"
    # por charla y el recordatorio seguía sonando cada X min (bug real).
    # Solo actúa si HAY un recordatorio insistente, para no disparar por error.
    if re.search(
        r"\b(p[aá]usa\w*|paus[ae]r|det[eé]n\w*|deten\w*|silenci\w*|"
        r"deja de (?:recordar|insistir|avisar)\w*|"
        r"para de (?:recordar|insistir|avisar)\w*|"
        r"ya no me (?:recuerdes|avises|insistas)|"
        r"yo te aviso|te aviso (?:yo|luego|despu[eé]s))\b", low):
        rec = db.listar_recordatorios()
        insistentes = [r for r in rec if r.get("insistir_min")]
        if insistentes:
            for r in insistentes:
                db.silenciar_recordatorio(r["id"])
            if len(insistentes) == 1:
                return (f"🔕 Listo, dejo de insistir con <i>{esc(insistentes[0]['texto'])}</i>. "
                        "Avísame cuando quieras retomarlo.", False)
            return (f"🔕 Listo, pausé la insistencia de {len(insistentes)} recordatorios. "
                    "Avísame cuando quieras retomarlos.", False)

    if estricto:
        return None  # que decida la IA

    return ("🤔 No entendi esa. Prueba con frases como:\n"
            "  ⏰ <i>recuérdame X mañana a las 10</i>\n"
            "  📝 <i>agrega comprar pan</i>\n"
            "o escribe <b>ayuda</b>.", False)


# ------------------------------------------------------------ acciones de IA
def _fecha_ok(s):
    """True si s es una fecha real con formato AAAA-MM-DD."""
    try:
        datetime.datetime.strptime((s or "").strip(), "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _hora_ok(s):
    """True si s es una hora real HH:MM (24h). Vacio/None se considera valido."""
    if s in (None, "", "null", "none"):
        return True
    try:
        datetime.datetime.strptime(str(s).strip(), "%H:%M")
        return True
    except (ValueError, TypeError):
        return False


def _cuando_ok(s):
    """True si s es un instante real AAAA-MM-DDTHH:MM."""
    try:
        datetime.datetime.strptime((s or "").strip(), "%Y-%m-%dT%H:%M")
        return True
    except (ValueError, TypeError):
        return False


def ejecutar_acciones(acciones, tareas):
    """Aplica las acciones de Gemini. Devuelve (lineas, hubo_cambio, preguntas).
    'preguntas' son mensajes con botones a enviar aparte (ej. preguntar cuántas
    veces insistir un recordatorio)."""
    lineas = []
    cambio = False
    preguntas = []
    grupos_vistos = set()
    for a in acciones:
        if not isinstance(a, dict):
            continue
        tipo = a.get("tipo")
        if tipo == "agregar_pendiente":
            txt = (a.get("texto") or "").strip()
            if txt:
                tareas.setdefault("pendientes", []).append(txt)
                lineas.append(f"📝 Agregado: <i>{esc(txt)}</i>")
                cambio = True
        elif tipo == "borrar_pendiente":
            q = quitar_pendiente(tareas, str(a.get("objetivo", "")))
            if q:
                db.log_actividad("pendiente", q)
            lineas.append(f"✅ Listo: <i>{esc(q)}</i>" if q else "🤔 No encontré ese pendiente.")
            cambio = cambio or bool(q)
        elif tipo == "agregar_evento":
            if not _fecha_ok(a.get("fecha")) or not _hora_ok(a.get("hora")):
                lineas.append(f"🤔 No agendé <i>{esc((a.get('titulo') or '').strip())}</i>: la IA dio una fecha u hora inválida.")
                continue
            nuevo = {"fecha": a.get("fecha", ""), "titulo": (a.get("titulo") or "").strip()}
            if a.get("hora"):
                nuevo["hora"] = a["hora"]
            tareas.setdefault("eventos", []).append(nuevo)
            hora = f" · {esc(nuevo['hora'])}" if nuevo.get("hora") else ""
            lineas.append(f"📅 Agendado: <i>{esc(nuevo['titulo'])}</i>\n      🗓 {esc(nuevo['fecha'])}{hora}")
            cambio = True
        elif tipo == "borrar_evento":
            q = quitar_evento(tareas, str(a.get("objetivo", "")))
            lineas.append(f"🗑 Evento borrado: <i>{esc(q.get('titulo',''))}</i>" if q else "🤔 No encontré ese evento.")
            cambio = cambio or bool(q)
        elif tipo == "agregar_recordatorio":
            cuando = a.get("cuando", "")
            txt = (a.get("texto") or "").strip()
            rep = a.get("repetir") or None
            if rep in ("null", "", "none"):
                rep = None
            # Intervalo de insistencia que sugiere la IA (en min). No insiste
            # solo: el recordatorio nace avisando UNA vez y, si la IA detectó
            # intención de insistir, se le PREGUNTA al usuario cuántas veces.
            inter = a.get("insistir_min")
            try:
                inter = int(inter) if inter not in (None, "null", "", "none", 0, "0") else None
            except (ValueError, TypeError):
                inter = None
            if inter and inter < 1:
                inter = None
            grupo = a.get("grupo")
            if grupo in ("null", "", "none"):
                grupo = None
            if not _cuando_ok(cuando):
                lineas.append(f"🤔 No creé el recordatorio <i>{esc(txt)}</i>: la IA dio una fecha/hora inválida.")
                continue
            # Evita ráfagas: en un plan escalonado, omite los avisos cuya hora
            # ya pasó hace más de 5 min (se dispararían todos de golpe).
            if grupo:
                try:
                    cuando_dt = datetime.datetime.strptime(cuando, "%Y-%m-%dT%H:%M")
                    if (datetime.datetime.now() - cuando_dt).total_seconds() > 300:
                        continue
                except (ValueError, TypeError):
                    pass
            # Nace con insistir_veces=0: avisa una sola vez salvo que el usuario
            # elija insistir por los botones de abajo.
            rid = db.add_recordatorio(cuando, txt, rep, insistir_min=inter,
                                      grupo=grupo, insistir_veces=0)
            if grupo:
                if grupo not in grupos_vistos:
                    grupos_vistos.add(grupo)
                    lineas.append("📋 <b>Plan de avisos creado</b> (marca Hecho en cualquiera y se apagan todos):")
                lineas.append(f"  🕐 {esc(cuando.replace('T',' · '))} — <i>{esc(txt)}</i>")
                continue
            extra = f"\n      🔁 se repite {esc(rep)}" if rep else ""
            lineas.append(f"⏰ Recordatorio: <i>{esc(txt)}</i>\n      🕐 {esc(cuando.replace('T',' · '))}{extra}")
            if inter:
                cada = (f"cada {inter} min" if inter < 60
                        else f"cada {inter // 60} h")
                botones_ins = [
                    [{"text": "1 vez", "callback_data": f"ins_set:{rid}:{inter}:1"},
                     {"text": "3 veces", "callback_data": f"ins_set:{rid}:{inter}:3"}],
                    [{"text": "5 veces", "callback_data": f"ins_set:{rid}:{inter}:5"},
                     {"text": "No insistir", "callback_data": f"ins_set:{rid}:{inter}:0"}],
                    [{"text": "🔥 Súper insistente",
                      "callback_data": f"ins_set:{rid}:{inter}:-1"}],
                ]
                preguntas.append((
                    f"🔔 ¿Cuántas veces te insisto con <i>{esc(txt)}</i> "
                    f"({cada}) si no respondes?", botones_ins))
        elif tipo == "borrar_recordatorio":
            q = db.borrar_recordatorio(a.get("objetivo", ""))
            if q:
                msg = f"✅ Recordatorio apagado: <i>{esc(q['texto'])}</i>"
                if q.get("borrados", 0) > 1:
                    msg += f" (y sus {q['borrados']} avisos 🔕)"
                lineas.append(msg)
            else:
                lineas.append("🤔 No encontré ese recordatorio.")
        elif tipo == "listar_recordatorios":
            rec = db.listar_recordatorios()
            if rec:
                lineas.append("⏰ <b>Tus recordatorios</b>\n" + "\n".join(
                    f"  • {esc(r['texto'])}\n      🕐 {esc(r['cuando'].replace('T',' · '))}" for r in rec))
            else:
                lineas.append("⏰ No tienes recordatorios. ✨")
        elif tipo == "crear_proyecto":
            db.add_proyecto((a.get("nombre") or "").strip(), (a.get("descripcion") or "").strip())
            lineas.append(f"🏗 Proyecto creado: <b>{esc(a.get('nombre',''))}</b>")
        elif tipo == "agregar_fase":
            minutos = a.get("minutos")
            if minutos in ("null", "", "none", 0):
                minutos = None
            db.add_fase(a.get("proyecto", ""), (a.get("titulo") or "").strip(),
                        (a.get("contexto") or "").strip(), minutos=minutos)
            lineas.append(f"➕ Fase agregada a <b>{esc(a.get('proyecto',''))}</b>: <i>{esc(a.get('titulo',''))}</i>")
        elif tipo == "completar_fase":
            proy = a.get("proyecto", "")
            comp, sig = db.completar_fase(proy)
            if comp:
                db.log_actividad("fase", comp["titulo"])
                msg = f"✅ <b>Fase completada:</b> {esc(comp['titulo'])}"
                prog = db.progreso_proyecto(proy)
                if prog:
                    msg += f"\n      📊 Vas {prog[0]} de {prog[1]} ✊"
                r = db.racha()
                if r > 1:
                    msg += f"\n      🔥 Racha: {r} días seguidos avanzando!"
                msg += f"\n      ▶️ Sigue: <i>{esc(sig['titulo'])}</i>" if sig else "\n      🎉 <b>Proyecto terminado!</b>"
                lineas.append(msg)
            else:
                lineas.append("🤔 No encontré fases pendientes en ese proyecto.")
        elif tipo == "siguiente_fase":
            f = db.fase_actual(a.get("proyecto", ""))
            if f:
                ctx = f"\n      🏷 {esc(f['contexto'])}" if f["contexto"] else ""
                mins = f" (~{f['minutos']} min)" if f["minutos"] else ""
                lineas.append(f"▶️ Siguiente fase de <b>{esc(a.get('proyecto',''))}</b>:\n"
                              f"      {esc(f['titulo'])}{mins}{ctx}")
            else:
                lineas.append("✨ Ese proyecto no tiene fases pendientes.")
        elif tipo == "listar":
            lineas.append(texto_lista(tareas))
        elif tipo == "resumen":
            lineas.append(A.construir_resumen(tareas, datetime.date.today()))
        elif tipo == "agregar_nota":
            txt = (a.get("texto") or "").strip()
            if txt:
                db.add_nota(txt)
                lineas.append(f"🗒 Nota guardada: <i>{esc(txt)}</i>")
        elif tipo == "buscar_nota":
            notas = db.buscar_notas(str(a.get("objetivo", "")))
            if notas:
                lineas.append("🗒 <b>Notas</b>\n" + "\n".join(
                    f"  {i}. {esc(n['texto'])}\n      📆 {n['fecha'][:10]}"
                    for i, n in enumerate(notas, 1)))
            else:
                lineas.append("🤔 No encontré notas con eso.")
        elif tipo == "borrar_nota":
            q = db.borrar_nota(a.get("objetivo", ""))
            lineas.append(f"🗑 Nota borrada: <i>{esc(q['texto'])}</i>" if q else "🤔 No encontré esa nota.")
        elif tipo == "agregar_interes":
            txt = (a.get("texto") or "").strip()
            if txt:
                db.add_interes(txt)
                lineas.append(f"🎯 Interes guardado: <i>{esc(txt)}</i>")
        elif tipo == "borrar_interes":
            q = db.borrar_interes(a.get("objetivo", ""))
            lineas.append(f"🗑 Interes borrado: <i>{esc(q)}</i>" if q else "🤔 No encontré ese interes.")
        elif tipo == "listar_intereses":
            lineas.append(texto_intereses())
        elif tipo == "guardar_lectura":
            db.set_lectura(a.get("nombre", "lectura"), a.get("marcador", ""))
            lineas.append(f"📖 Anotado: <b>{esc(a.get('nombre',''))}</b> → <i>{esc(a.get('marcador',''))}</i>")
        elif tipo == "ver_lectura":
            lect = db.get_lecturas()
            if lect:
                lineas.append("📖 <b>Lecturas</b>\n" + "\n".join(
                    f"  • <b>{esc(l['nombre'])}</b>: {esc(l['marcador'])}\n      📆 {l['actualizado']}"
                    for l in lect))
            else:
                lineas.append("📖 No tienes lecturas registradas.")
    return lineas, cambio, preguntas


# ----------------------------------------------------- hilo de recordatorios
DESPERTAR = threading.Event()  # se activa cuando llega un mensaje (pudo crear recordatorios)

HORAS_PROACTIVO = (9, 21)   # solo sugiere entre estas horas
CADA_PROACTIVO = 4 * 3600   # minimo 4h entre sugerencias y desde tu ultima actividad


def sugerencia_proactiva(token, chat_ids):
    """Si llevas horas sin escribir y hay fases pendientes, te propone una."""
    ahora = time.time()
    h = datetime.datetime.now().hour
    if not (HORAS_PROACTIVO[0] <= h < HORAS_PROACTIVO[1]):
        return
    ultima_act = float(db.estado_get("ultima_actividad", 0) or 0)
    ultima_sug = float(db.estado_get("ultima_sugerencia", 0) or 0)
    if ahora - ultima_act < CADA_PROACTIVO or ahora - ultima_sug < CADA_PROACTIVO:
        return
    proyectos = db.cargar_proyectos(solo_pendientes=True)
    candidatas = []
    for p in proyectos:
        if p["fases"]:
            f = p["fases"][0]
            candidatas.append((p["nombre"], f))
    if not candidatas:
        return
    nombre, f = min(candidatas, key=lambda x: x[1].get("minutos") or 999)
    mins = f" (~{f['minutos']} min)" if f.get("minutos") else ""
    ctx = f" [{f['contexto']}]" if f.get("contexto") else ""
    msg = (f"👋 ¿Tienes un rato? Podrias avanzar <b>{esc(nombre)}</b>:\n"
           f"  ▶️ {esc(f['titulo'])}{mins}{esc(ctx)}\n"
           f"Si la haces, dime <i>complete la fase de {esc(nombre)}</i>.")
    for cid in chat_ids:
        A.enviar_mensaje(msg, token, cid)
    db.estado_set("ultima_sugerencia", ahora)


# Umbrales de salud de servicios (proactivo, no solo registrar):
IA_FALLOS_ALERTA = 5      # fallos de IA seguidos antes de avisar
LATIDO_MAX_S = 900        # 15 min sin hablar con Telegram = sin red/atascado


def salud_servicios(ahora=None):
    """Devuelve una lista de problemas de SERVICIO (no de hardware) para que el
    bot AVISE en vez de solo registrar:
      - la IA falla de forma sostenida (todas las IAs caídas o sin cuota),
      - lleva demasiado tiempo sin poder hablar con Telegram (sin red/atascado).
    El aviso real (con su límite de 1/hora) lo hace el bucle que la llama."""
    if ahora is None:
        ahora = time.time()
    problemas = []
    fallos = int(float(db.estado_get("ia_fallos_seguidos", 0) or 0))
    if fallos >= IA_FALLOS_ALERTA:
        problemas.append(f"🧠 La IA lleva {fallos} fallos seguidos "
                         "(sin respuesta de ninguna IA: red o cuotas).")
    latido = float(db.estado_get("latido", 0) or 0)
    if latido and ahora - latido > LATIDO_MAX_S:
        mins = int((ahora - latido) / 60)
        problemas.append(f"📡 Sin contacto con Telegram desde hace {mins} min "
                         "(¿sin red o el polling atascado?).")
    return problemas


def salud_texto(ahora=None):
    """Resumen de SALUD DEL BOT (no de hardware) para mostrar en /estado:
    último latido con Telegram, fallos de IA en cadena y última actividad.
    Solo lectura del estado que el propio bot ya guarda en la BD."""
    if ahora is None:
        ahora = time.time()
    out = ["🤖 <b>Salud del bot</b>"]

    latido = float(db.estado_get("latido", 0) or 0)
    if latido:
        seg = int(ahora - latido)
        icono = "🟢" if seg <= LATIDO_MAX_S else "🔴"
        if seg < 90:
            cuando = f"hace {seg} s"
        elif seg < 5400:
            cuando = f"hace {seg // 60} min"
        else:
            cuando = f"hace {seg // 3600} h"
        out.append(f"  {icono} Telegram: último contacto {cuando}")
    else:
        out.append("  ⚪ Telegram: aún sin primer latido")

    fallos = int(float(db.estado_get("ia_fallos_seguidos", 0) or 0))
    icono = "🟢" if fallos == 0 else ("🟡" if fallos < IA_FALLOS_ALERTA else "🔴")
    if fallos == 0:
        out.append(f"  {icono} IA: respondiendo bien")
    else:
        out.append(f"  {icono} IA: {fallos} fallo(s) seguido(s)")

    act = float(db.estado_get("ultima_actividad", 0) or 0)
    if act:
        seg = int(ahora - act)
        cuando = f"{seg // 60} min" if seg >= 90 else f"{seg} s"
        out.append(f"  💬 Última actividad tuya: hace {cuando}")

    problemas = salud_servicios(ahora)
    if problemas:
        out.append("  ⚠️ <b>Avisos:</b>")
        out.extend(f"     {p}" for p in problemas)
    return "\n".join(out)


def vigilar_recordatorios(token, cfg, parar):
    """Envia los recordatorios vencidos (de CUALQUIER usuario, cada uno a su
    chat) y duerme justo hasta el proximo. Si llega un mensaje, se reevalua."""
    # Este hilo SIEMPRE trabaja como dueño principal (respaldo, sugerencias,
    # salud); para cada recordatorio elige el chat por el dueño de la fila. Es
    # un default fijo de por vida del hilo, no el patrón frágil de ir cambiando
    # de dueño entre mensajes (eso ahora va con db.como_dueno() en el bucle).
    db.set_dueno(db.DUENO_PRINCIPAL)
    while not parar.is_set():
        espera = 300  # tope: 5 min
        try:
            db.respaldo_diario()
            # Cada recordatorio se entrega a su hora. La franja de silencio
            # difiere a la mañana: (a) las entregas cuya hora NO la fijó el
            # usuario a propósito (posponer_madrugada, evita sorpresas de
            # madrugada) y (b) las RE-insistencias automáticas (db.marcar_enviado).
            silencio = _franja_silencio(cfg)
            db.posponer_madrugada(silencio)
            for r in db.recordatorios_vencidos():  # de todos los dueños
                botones = [[
                    {"text": "✅ Hecho", "callback_data": f"rec_done:{r['id']}"},
                    {"text": "⏰ +30 min", "callback_data": f"rec_post:{r['id']}"},
                ]]
                for cid in destinos_de(r.get("dueno") or db.DUENO_PRINCIPAL, cfg):
                    A.enviar_mensaje(
                        f"⏰ <b>Permítame recordarle:</b> {esc(r['texto'])}",
                        token, cid, botones=botones)
                db.marcar_enviado(r, silencio=silencio)
            # (sugerencia proactiva desactivada: resultaba molesta)
            # Salud de la maquina y de los SERVICIOS: si algo esta critico,
            # avisa al admin (max 1 vez/hora). Incluye IA caida y sin red.
            try:
                problemas = sistema.alertas() + salud_servicios()
                ult = float(db.estado_get("ult_alerta_sistema", 0) or 0)
                if problemas and time.time() - ult > 3600:
                    msg = ("⚠️ <b>Aviso técnico de la máquina</b>\n"
                           "Me permito señalarle lo siguiente:\n"
                           + "\n".join(f"  {p}" for p in problemas))
                    for cid in creador(cfg):  # técnico: solo al creador
                        A.enviar_mensaje(msg, token, cid)
                    db.estado_set("ult_alerta_sistema", time.time())
            except Exception as e:
                log.warning("Error revisando salud de la maquina: %s", e)
            prox = db.proximo_recordatorio()
            if prox:
                falta = (datetime.datetime.strptime(prox, "%Y-%m-%dT%H:%M")
                         - datetime.datetime.now()).total_seconds()
                espera = max(1, min(espera, falta))
        except Exception as e:
            log.warning("Error en el hilo de recordatorios: %s", e)
            espera = 30
        DESPERTAR.clear()
        DESPERTAR.wait(espera)
        if parar.is_set():
            break


def _texto_metricas():
    """Resumen legible de los contadores para el administrador."""
    m = db.metricas()
    if not m:
        return "📊 Aún no hay métricas registradas."
    msgs = m.get("mensajes", 0)
    bot_p = m.get("botones", 0)
    ia_n = m.get("ia_n", 0)
    ia_fall = m.get("ia_fallos", 0)
    ia_prom = m.get("ia_ms_prom")
    tasa = f"{100 * ia_fall / (ia_n + ia_fall):.0f}%" if (ia_n + ia_fall) else "—"
    lineas = [
        "📊 <b>Métricas</b>",
        f"  💬 Mensajes: <b>{msgs}</b>",
        f"  🔘 Botones: <b>{bot_p}</b>",
        f"  🤖 Llamadas IA OK: <b>{ia_n}</b>",
        f"  ⚠️ Fallos IA: <b>{ia_fall}</b> (tasa {tasa})",
    ]
    if ia_prom is not None:
        lineas.append(f"  ⏱ Latencia IA media: <b>{ia_prom} ms</b>")
    return "\n".join(lineas)


# ------------------------------------------------------- presentacion (nombre)
# A partir de esta fecha, la PRIMERA vez que alguien (que aun no haya dado su
# nombre) escriba, Larry se presenta y le pregunta como desea que se dirija a el.
# Luego alterna entre 'señor <Nombre>' y 'señor' a secas (db.tratamiento).
ONBOARDING_DESDE = datetime.date(2026, 6, 14)


def _clave_esperando_nombre(chat_id):
    return "esperando_nombre:" + str(chat_id)


def _extraer_nombre(texto):
    """Saca un nombre razonable de lo que escribio el usuario. Acepta 'Samuel',
    'me llamo Samuel', 'soy Samuel', 'mi nombre es Samuel'. Devuelve None si no
    parece un nombre (vacio, demasiado largo o un comando)."""
    t = (texto or "").strip()
    if not t or t.startswith("/"):
        return None
    m = re.match(r"^(?:me\s+llamo|soy|mi\s+nombre\s+es|ll[aá]mame|puedes?\s+"
                 r"llamarme|dime)\s+(.+)$", t, re.I)
    if m:
        t = m.group(1).strip()
    t = t.strip(" .,:;¡!¿?\"'").strip()
    if not t or len(t) > 40 or "\n" in t:
        return None
    return t


def _saludo_presentacion():
    return (
        "Antes de proseguir, permítame una cortesía: ¿cómo desea que me dirija "
        "a usted? Indíqueme su nombre, se lo ruego."
    )


def _onboarding(texto, cfg, token, chat_id):
    """Pide y registra el nombre del usuario la primera vez. Devuelve True si ya
    gestiono el mensaje (hay que cortar el flujo); False para seguir normal."""
    if datetime.date.today() < ONBOARDING_DESDE:
        return False
    clave = _clave_esperando_nombre(chat_id)
    if db.estado_get(clave) == "1":
        # Estamos esperando su nombre: este mensaje ES la respuesta.
        nombre = _extraer_nombre(texto)
        if not nombre:
            A.enviar_mensaje(
                "Disculpe, no logré captar su nombre. ¿Cómo desea que me "
                "dirija a usted?", token, chat_id)
            return True
        db.set_nombre(nombre)
        db.estado_set(clave, "0")
        A.enviar_mensaje(
            f"Un placer, señor {esc(nombre)}. Quedo a su entero servicio.",
            token, chat_id)
        return True
    if db.get_nombre() is None:
        # Aun no se ha presentado: nos presentamos y se lo preguntamos.
        db.estado_set(clave, "1")
        A.enviar_mensaje(_saludo_presentacion(), token, chat_id)
        return True
    return False


# ----------------------------------------------------------- procesar mensaje
def manejar_mensaje(texto, cfg, token, chat_id, prefijo=""):
    db.metrica_inc("mensajes")
    tareas = db.cargar_tareas()

    # 0.0) Presentacion: la primera vez, Larry pregunta el nombre del usuario.
    if _onboarding(texto, cfg, token, chat_id):
        return

    # 0) Atajos con botones (menu minimalista).
    low = texto.strip().lower()
    if low in ("menu", "/menu", "m"):
        A.enviar_mensaje("👇 <b>¿Qué hacemos?</b>", token, chat_id,
                         botones=botones_menu())
        return
    if low in ("metricas", "/metricas", "métricas", "/métricas"):
        # Diagnostico tecnico: solo el creador lo ve.
        if es_admin(chat_id, cfg):
            A.enviar_mensaje(_texto_metricas(), token, chat_id)
        else:
            A.enviar_mensaje("🔒 Ese comando es solo para el administrador.",
                             token, chat_id)
        return
    if low in ("estado", "/estado", "salud", "maquina", "máquina") or \
            re.search(r"(como|cómo)\s+esta\s+la\s+(lenovo|maquina|máquina|compu)", low):
        # Diagnostico de la maquina/servidor: SOLO el creador (no es para los
        # usuarios externos; expone RAM, disco, temperatura, latido del bot...).
        if es_admin(chat_id, cfg):
            A.enviar_mensaje(sistema.estado_texto() + "\n\n" + salud_texto(),
                             token, chat_id)
        else:
            A.enviar_mensaje("Ese comando está reservado al administrador.",
                             token, chat_id)
        return
    if low in ("usuarios", "/usuarios", "cuantos usuarios", "cuántos usuarios"):
        # Diagnostico tecnico: solo el creador lo ve.
        if es_admin(chat_id, cfg):
            u = db.contar_usuarios()
            externos = u["externos"]
            lineas = ["<b>Usuarios con datos en el servidor</b>"]
            lineas.append(f"  • Total de espacios: <b>{u['total']}</b>")
            lineas.append("  • Sus cuentas personales (principal): "
                          + ("sí" if u["tiene_principal"] else "no"))
            lineas.append(f"  • Usuarios externos: <b>{len(externos)}</b>")
            if externos:
                lineas.append("    " + ", ".join(esc(d) for d in externos))
            A.enviar_mensaje("\n".join(lineas), token, chat_id)
        else:
            A.enviar_mensaje("Ese comando está reservado al administrador.",
                             token, chat_id)
        return
    if low in ("proyectos", "/proyectos", "mis proyectos"):
        A.enviar_mensaje(texto_proyectos(), token, chat_id,
                         botones=botones_proyectos())
        return
    if low in ("intereses", "/intereses", "mis intereses"):
        A.enviar_mensaje(texto_intereses(), token, chat_id)
        return

    # 0.4) Borron total recuperable. Por seguridad NUNCA se borra desde texto:
    #      el bot manda SIEMPRE a confirmar con los botones (evita un borrado
    #      accidental por una frase suelta). Se puede deshacer durante 24h.
    if re.search(r"^/?(?:borra(?:r)?|elimina(?:r)?|limpia(?:r)?)\s+todo(?:s)?\b",
                 low) or low in ("/borrartodo", "borrón total", "borron total"):
        A.enviar_mensaje(
            "⚠️ <b>¿Seguro que quieres borrar TODO?</b>\n"
            "Se eliminarán tus recordatorios, tareas, eventos, notas, proyectos, "
            "fases y lecturas.\n\n"
            "🛟 Tranquilo: queda guardado <b>24 horas</b> por si te arrepientes.\n"
            "👇 Confírmalo con los botones (no se borra escribiéndolo):",
            token, chat_id, botones=[[
                {"text": "🗑 Sí, borrar TODO", "callback_data": "borrar_all:si"},
                {"text": "✖️ Cancelar", "callback_data": "borrar_all:no"},
            ]])
        return
    if low in ("recuperar", "/recuperar", "recupera todo", "recuperar todo",
               "deshacer", "restaurar", "restaurar todo", "deshacer borrado"):
        n = db.recuperar_todo()
        if n:
            A.enviar_mensaje(
                f"↩️ <b>Restaurado.</b> Recuperé {n} elemento(s). Todo vuelve a su sitio.",
                token, chat_id)
        else:
            A.enviar_mensaje(
                "🤷 No hay nada que recuperar (no borraste nada en las últimas 24h).",
                token, chat_id)
        return

    # 0.5) Busqueda web fiable: "busca X" / "investiga X"
    m = re.match(r"^/?(?:busca(?:me|r)?|investiga)\s+(.+)$", texto.strip(), re.I)
    if m:
        consulta = m.group(1).strip(" ?¿!.")
        resultados = busqueda.buscar(consulta)
        if not resultados:
            A.enviar_mensaje("🔍 No encontré nada en la web sobre eso.", token, chat_id)
            return
        api_key = cfg.get("gemini_api_key", "").strip()
        pausa = float(db.estado_get("ia_pausada_hasta", 0) or 0)
        if gemini_ia and api_key:
            try:
                db.uso_inc()
                resp = gemini_ia.responder_busqueda(
                    consulta, resultados, api_key, cfg=cfg,
                    usar_gemini=time.time() >= pausa)
                A.enviar_mensaje("🔍 " + resp, token, chat_id)
                return
            except Exception as e:
                log.warning("Busqueda con IA fallo, uso resultados crudos: %s", e)
        # Sin IA: mando los resultados crudos con sus links
        lineas = ["🔍 <b>Encontré esto:</b>"]
        for r in resultados[:4]:
            lineas.append(f"\n• <b>{esc(r['titulo'])}</b>\n  {esc(r['resumen'][:200])}\n  🔗 {esc(r['url'])}")
        A.enviar_mensaje("\n".join(lineas), token, chat_id)
        return

    # 1) Atajos instantaneos (lista, resumen, ayuda): no gastan cuota de IA.
    rapido = atajo(texto, tareas)
    if rapido is not None:
        respuesta, cambio = rapido
        if cambio:
            db.guardar_tareas(tareas)
        A.enviar_mensaje(prefijo + respuesta, token, chat_id)
        return

    # 2) Parser por reglas en modo estricto: comandos claros se resuelven
    #    al instante y GRATIS, sin gastar cuota de IA.
    simple = procesar_simple(texto, tareas, estricto=True)
    if simple is not None:
        respuesta, cambio = simple
        if cambio:
            db.guardar_tareas(tareas)
        A.enviar_mensaje(prefijo + respuesta, token, chat_id)
        return

    # 3) Gemini: proyectos, fases, sugerencias y frases libres,
    #    con memoria de la conversacion reciente.
    api_key = cfg.get("gemini_api_key", "").strip()
    pausa = float(db.estado_get("ia_pausada_hasta", 0) or 0)
    if gemini_ia and api_key:
        try:
            proyectos = db.cargar_proyectos(solo_pendientes=True)
            for p in proyectos:  # solo las 3 proximas fases: menos tokens
                p["fases"] = p["fases"][:3]
                p.pop("descripcion", None)
            historial = db.historial_reciente(chat_id, 6)
            extras = {
                "notas_recientes": [n["texto"][:80] for n in db.buscar_notas()[:3]],
                "lecturas": db.get_lecturas(),
                "racha_dias": db.racha(),
                "intereses_personales": db.get_intereses(),
            }
            db.uso_inc()
            _t0 = time.time()
            acciones, frase = gemini_ia.interpretar(
                texto, tareas, api_key, proyectos=proyectos,
                historial=historial, extras=extras, cfg=cfg,
                usar_gemini=time.time() >= pausa,
                trato=db.tratamiento())
            db.metrica_observar("ia", (time.time() - _t0) * 1000)
            db.estado_set("ia_fallos_seguidos", 0)  # respondió: cadena rota
            lineas, cambio, preguntas = ejecutar_acciones(acciones, tareas)
            respuesta = frase + (("\n\n" + "\n".join(lineas)) if lineas else "")
            if cambio:
                db.guardar_tareas(tareas)
            db.historial_add(chat_id, "user", texto)
            db.historial_add(chat_id, "model", respuesta)
            A.enviar_mensaje(prefijo + respuesta, token, chat_id)
            for ptxt, pbotones in preguntas:  # ej. "¿cuántas veces te insisto?"
                A.enviar_mensaje(ptxt, token, chat_id, botones=pbotones)
            return
        except Exception as e:
            # Llega aqui solo si Gemini Y TODOS los respaldos fallaron.
            db.metrica_inc("ia_fallos")
            db.estado_set("ia_fallos_seguidos",
                          int(float(db.estado_get("ia_fallos_seguidos", 0) or 0)) + 1)
            log.error("Todas las IAs fallaron: %s", e)
            A.enviar_mensaje(
                prefijo + "😴 Ninguna IA respondio (red o cuotas); "
                "intenta de nuevo en un rato.\n"
                "Mientras, entiendo comandos directos:\n"
                "  ⏰ <i>recuérdame X mañana a las 10</i>\n"
                "  📅 <i>agéndame X mañana 10am</i>\n"
                "  📋 <b>lista</b> · 🏗 <b>proyectos</b> · ☀️ <b>resumen</b>",
                token, chat_id)
            return

    # 4) Respaldo sin IA configurada.
    respuesta, cambio = procesar_simple(texto, tareas)
    if cambio:
        db.guardar_tareas(tareas)
    A.enviar_mensaje(prefijo + respuesta, token, chat_id)


# ------------------------------------------------------------------- botones
def manejar_boton(cb, cfg, token, chat_id):
    """Procesa los botones inline (recordatorios, proyectos y menu)."""
    db.metrica_inc("botones")
    data = cb.get("data", "")
    aviso = ""
    botones = None
    try:
        accion, rid = data.split(":", 1)
        if accion == "menu":
            try:
                A.api_telegram("answerCallbackQuery",
                               {"callback_query_id": cb["id"], "text": ""}, token)
            except Exception:
                pass
            if rid == "proyectos":
                A.enviar_mensaje(texto_proyectos(), token, chat_id,
                                 botones=botones_proyectos())
            elif rid == "nombre":
                # Re-pregunta el nombre: el proximo mensaje sera la respuesta.
                db.estado_set(_clave_esperando_nombre(chat_id), "1")
                actual = db.get_nombre()
                if actual:
                    A.enviar_mensaje(
                        f"Actualmente le llamo <b>{esc(actual)}</b>. ¿Cómo "
                        "desea que me dirija a usted en adelante?",
                        token, chat_id)
                else:
                    A.enviar_mensaje(_saludo_presentacion(), token, chat_id)
            elif rid == "sugerencia":
                manejar_mensaje(
                    "No tengo nada que hacer ahora, sugiereme algo concreto "
                    "segun mis proyectos e intereses", cfg, token, chat_id)
            else:  # lista, resumen, notas, recordatorios
                manejar_mensaje(rid, cfg, token, chat_id)
            return
        if accion == "proy_all":
            aviso = texto_proyectos(completo=True)
            botones = botones_proyectos()
        elif accion == "proy_done":
            comp, sig = db.completar_fase(rid)
            if comp:
                db.log_actividad("fase", comp["titulo"])
                aviso = f"✅ <b>Fase completada:</b> {esc(comp['titulo'])}"
                prog = db.progreso_proyecto(rid)
                if prog:
                    aviso += f"\n      📊 Vas {prog[0]} de {prog[1]} ✊"
                r = db.racha()
                if r > 1:
                    aviso += f"\n      🔥 Racha: {r} días seguidos!"
                aviso += (f"\n      ▶️ Sigue: <i>{esc(sig['titulo'])}</i>" if sig
                          else "\n      🎉 <b>Proyecto terminado!</b>")
            else:
                aviso = "🤔 Ese proyecto no tiene fases pendientes."
        elif accion == "proy_next":
            f = db.fase_actual(rid)
            if f:
                ctx = f"\n      🏷 {esc(f['contexto'])}" if f["contexto"] else ""
                mins = f" (~{f['minutos']} min)" if f["minutos"] else ""
                aviso = f"▶️ Siguiente fase:\n      <b>{esc(f['titulo'])}</b>{mins}{ctx}"
            else:
                aviso = "✨ Sin fases pendientes."
        elif accion == "rec_done":
            q = db.borrar_recordatorio_id(int(rid))
            if q:
                aviso = f"🎉 Bien hecho! ✅ <i>{esc(q['texto'])}</i>"
                if q.get("borrados", 0) > 1:
                    aviso += f"\n🔕 Apagué los {q['borrados']} avisos de esa tarea."
            else:
                aviso = "🤷 Ese recordatorio ya no existe."
        elif accion == "rec_post":
            nuevo = db.posponer_recordatorio(int(rid), 30)
            aviso = f"⏰ Pospuesto a las <b>{nuevo.split('T')[1]}</b>"
        elif accion == "borrar_all":
            if rid == "si":
                total, pid = db.borrar_todo()
                if total:
                    aviso = (f"🗑 <b>Borrado.</b> Eliminé {total} elemento(s).\n"
                             "🛟 Tienes <b>24 horas</b> para deshacerlo.")
                    botones = [[{"text": "↩️ Deshacer",
                                 "callback_data": f"recuperar_all:{pid}"}]]
                else:
                    aviso = "🤷 No tenías nada que borrar."
            else:
                aviso = "👍 Cancelado. No borré nada."
        elif accion == "recuperar_all":
            n = db.recuperar_todo(papelera_id=int(rid))
            if n:
                aviso = f"↩️ <b>Restaurado.</b> Recuperé {n} elemento(s)."
            else:
                aviso = ("🤷 Ya no se puede deshacer (pasaron 24h o ya lo "
                         "restauraste).")
        elif accion == "ins_set":
            # rid trae "id:intervalo:veces" (cuántas veces insistir).
            rec_id, inter, veces = (int(x) for x in rid.split(":"))
            db.configurar_insistencia(rec_id, inter, veces)
            cada = f"{inter} min" if inter < 60 else f"{inter // 60} h"
            if veces == -1:
                aviso = (f"🔥 Modo súper insistente: te avisaré cada {cada} "
                         "hasta que marques <b>Hecho</b> (de madrugada espero "
                         "a la mañana para no molestar).")
            elif veces > 0:
                aviso = (f"🔔 Listo: te insistiré hasta <b>{veces}</b> "
                         f"vez(ces) más, cada {cada}, si no marcas Hecho.")
            else:
                aviso = "👍 De acuerdo, te aviso una sola vez."
    except Exception as e:
        log.warning("Error procesando boton: %s", e)
        aviso = "🤔 No pude procesar el boton."
    # confirmar a Telegram que el boton fue atendido
    try:
        A.api_telegram("answerCallbackQuery",
                       {"callback_query_id": cb["id"], "text": ""}, token)
    except Exception:
        pass
    if aviso:
        A.enviar_mensaje(aviso, token, chat_id, botones=botones)


# --------------------------------------------------------------------- bucle
def tomar_lock():
    """Evita DOS instancias del bot en LA MISMA máquina (dos bots con el mismo
    token compiten y Telegram devuelve 409). Toma un lock exclusivo no
    bloqueante sobre un archivo; si ya está tomado, devuelve None. Hay que
    conservar el descriptor abierto mientras viva el proceso (por eso se
    devuelve y se guarda en una global)."""
    if fcntl is None:
        return True  # sin fcntl (no-POSIX): no podemos lockear, seguimos igual
    ruta = os.path.join(BASE_DIR, ".bot.lock")
    f = open(ruta, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f


def avisar_conflicto(token, cfg):
    """Avisa al admin (máx 1 vez/hora) de que OTRA instancia con el mismo token
    está corriendo en otra parte (Telegram da 409). El lock local solo cubre la
    misma máquina; esto cubre el caso de dos máquinas (p.ej. Dell + Lenovo)."""
    try:
        ult = float(db.estado_get("ult_aviso_409", 0) or 0)
        if time.time() - ult < 3600:
            return
        msg = ("⚠️ <b>Hay otra instancia activa con su mismo identificador</b> "
               "(Telegram responde 409 Conflict). Solo una máquina puede "
               "atenderle a la vez; le ruego apagar el bot en la máquina que no "
               "corresponda.")
        for cid in creador(cfg):
            A.enviar_mensaje(msg, token, cid)
        db.estado_set("ult_aviso_409", time.time())
    except Exception:
        pass


def main():
    # journald ya pone su timestamp; aqui solo nivel y nombre. LOG_LEVEL configurable.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    global _LOCK
    _LOCK = tomar_lock()
    if _LOCK is None:
        log.error("Ya hay otra instancia del bot corriendo en esta máquina "
                  "(lock tomado). Salgo para no competir por el token.")
        return
    cfg, token, _ = A.cargar_config()
    chat_ids = chat_ids_permitidos(cfg)
    if not token or not chat_ids:
        log.error("Falta token o chat_id en config.json.")
        return
    log.info("Cuentas permitidas: %s", ", ".join(chat_ids))
    notificar_actualizacion(token, creador(cfg))  # novedades SOLO al creador
    # Deja el changelog accesible para el parte matutino (proceso aparte): el
    # resumen lo anexará UNA vez por version (Larry anuncia sus propios cambios).
    db.estado_set("novedades_texto", NOVEDADES)
    db.estado_set("novedades_version", VERSION)

    parar = threading.Event()
    hilo = threading.Thread(target=vigilar_recordatorios, args=(token, cfg, parar), daemon=True)
    hilo.start()

    def apagar(*_):
        log.info("Apagando...")
        parar.set()
        DESPERTAR.set()  # despierta al hilo de recordatorios para que termine ya
    signal.signal(signal.SIGTERM, apagar)  # 'systemctl stop/restart' apaga limpio

    log.info("Bot escuchando... (Ctrl + C para apagar)")
    # El offset persiste en la BD: reiniciar el bot no reprocesa updates viejos.
    guardado = db.estado_get("tg_offset")
    offset = int(guardado) if guardado else None
    espera_error = 3  # backoff: se duplica ante errores, vuelve a 3 tras un ciclo OK
    conflictos = 0    # 409 seguidos (otra instancia con el mismo token)
    while not parar.is_set():
        try:
            params = {"timeout": 50,
                      "allowed_updates": '["message","callback_query"]'}
            if offset is not None:
                params["offset"] = offset
            url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            espera_error = 3  # ciclo exitoso: reinicia el backoff
            conflictos = 0    # hubo respuesta OK: no hay conflicto activo
            # Latido: prueba de que el bot está vivo y hablando con Telegram.
            # Un chequeador externo (chequear_salud.py) avisa si se queda viejo.
            db.estado_set("latido", time.time())

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                db.estado_set("tg_offset", offset)

                # Botones pulsados (✅ Hecho / ⏰ +30 min)
                cb = upd.get("callback_query")
                if cb:
                    emisor = str(cb["message"]["chat"]["id"])
                    ok, _ = permitido(emisor)
                    if not ok:
                        continue  # botones: descarta sin avisar (silencioso)
                    # Dueno explicito y acotado a este mensaje (se restaura solo).
                    with db.como_dueno(dueno_de(emisor, cfg)):
                        manejar_boton(cb, cfg, token, emisor)
                    DESPERTAR.set()
                    continue

                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                emisor = str(msg["chat"]["id"])
                dueno = dueno_de(emisor, cfg)

                if "text" in msg:
                    ok, avisar = permitido(emisor)
                    if not ok:
                        if avisar:
                            try:
                                A.enviar_mensaje(
                                    "⏳ Vas muy rápido. Espera un momento y "
                                    "vuelve a intentarlo.", token, emisor)
                            except Exception:
                                pass
                        log.warning("Rate-limit: descarto mensaje de %s", emisor)
                        continue
                    # Dueno explicito y acotado a este mensaje (se restaura solo).
                    with db.como_dueno(dueno):  # aisla los datos de cada usuario
                        if dueno == db.DUENO_PRINCIPAL:
                            db.estado_set("ultima_actividad", time.time())
                        manejar_mensaje(msg["text"], cfg, token, emisor)
                    DESPERTAR.set()  # por si el mensaje creo/borro recordatorios

        except KeyboardInterrupt:
            apagar()
            break
        except urllib.error.HTTPError as e:
            if e.code == 409:
                conflictos += 1
                # Tras varios 409 seguidos avisamos al admin (no es un fallo de
                # red pasajero: hay otra instancia con el mismo token).
                if conflictos >= 3:
                    avisar_conflicto(token, cfg)
            log.warning("Error en getUpdates (reintento en %ss): %s", espera_error, e)
            time.sleep(espera_error)
            espera_error = min(espera_error * 2, 60)
        except Exception as e:
            log.warning("Error en getUpdates (reintento en %ss): %s", espera_error, e)
            time.sleep(espera_error)
            espera_error = min(espera_error * 2, 60)  # 3→6→12→…→60s


if __name__ == "__main__":
    main()
