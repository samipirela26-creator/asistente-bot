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
import tempfile
import threading
import datetime
import urllib.parse
import urllib.request
from html import escape as esc

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

# Sube este numero cada vez que cambies el bot y escribe que cambio en NOVEDADES.
# Al arrancar, si la version es nueva, el bot te avisa por Telegram una sola vez.
VERSION = "2.3"
NOVEDADES = (
    "🚀 <b>Bot actualizado · v2.3</b>\n\n"
    "• 🖥️ <b>Nuevo hogar:</b> ahora corro desde el Lenovo, encendido 24/7, "
    "para estar siempre disponible.\n"
    "• 🔄 <b>Auto-actualización:</b> cada cambio que se sube se instala solo, "
    "sin que tengas que hacer nada.\n"
    "• 🩹 <b>Más estable:</b> arreglé una fuga de conexiones que tumbaba los "
    "recordatorios; ahora puedo correr días sin fallar.\n\n"
    "Tus datos llegaron completos. Sigo avisándote aquí cada actualización. 💪"
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


def destinos_de(dueno, cfg):
    """A qué chats hay que enviarle algo a un dueño (ej. sus recordatorios).
    'principal' -> todas tus cuentas; otro usuario -> solo su chat."""
    if dueno == db.DUENO_PRINCIPAL:
        return chat_ids_permitidos(cfg)
    return [dueno]


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
    msg = (f"🤖 <b>Uso de la IA</b> (limite gratis ≈ 250/dia)\n{cuerpo}\n"
           f"  Σ ultimos 7 dias: <b>{total}</b>")
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
            "⏰ <i>recuerdame llamar al banco manana a las 10</i>\n"
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

    if low in ("estado", "/estado", "salud", "maquina") or \
            re.search(r"(como|cómo)\s+esta\s+la\s+(lenovo|maquina|máquina|compu)", low):
        return sistema.estado_texto(), False

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
        return (f"🗑 Listo, elimine el evento: <i>{esc(q.get('titulo',''))}</i>", True) if q \
            else ("🤔 No encontre ese evento. Escribe <b>lista</b>.", False)

    m = re.match(r"^(borra|elimina|quita)\s+(?:pendiente\s+|tarea\s+)?(.+)$", low)
    if m:
        q = quitar_pendiente(tareas, t[m.start(2):])
        return (f"✅ Listo, borre: <i>{esc(q)}</i>", True) if q \
            else ("🤔 No encontre ese pendiente. Escribe <b>lista</b>.", False)

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
        db.add_recordatorio(cuando, txt, rep)
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
                msg += f"\n      🔥 Racha: {r2} dias seguidos!"
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
            return (f"🎉 Bien hecho! Apague el recordatorio: <i>{esc(q['texto'])}</i>", False)
        if objetivo:
            q = db.borrar_recordatorio(objetivo)
            if q:
                return (f"🎉 Bien hecho! Apague el recordatorio: <i>{esc(q['texto'])}</i>", False)

    if estricto:
        return None  # que decida la IA

    return ("🤔 No entendi esa. Prueba con frases como:\n"
            "  ⏰ <i>recuerdame X manana a las 10</i>\n"
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
    """Aplica las acciones de Gemini. Devuelve (lineas, hubo_cambio_en_tareas)."""
    lineas = []
    cambio = False
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
            lineas.append(f"✅ Listo: <i>{esc(q)}</i>" if q else "🤔 No encontre ese pendiente.")
            cambio = cambio or bool(q)
        elif tipo == "agregar_evento":
            if not _fecha_ok(a.get("fecha")) or not _hora_ok(a.get("hora")):
                lineas.append(f"🤔 No agende <i>{esc((a.get('titulo') or '').strip())}</i>: la IA dio una fecha u hora invalida.")
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
            lineas.append(f"🗑 Evento borrado: <i>{esc(q.get('titulo',''))}</i>" if q else "🤔 No encontre ese evento.")
            cambio = cambio or bool(q)
        elif tipo == "agregar_recordatorio":
            cuando = a.get("cuando", "")
            txt = (a.get("texto") or "").strip()
            rep = a.get("repetir") or None
            if rep in ("null", "", "none"):
                rep = None
            insistir = a.get("insistir_min")
            if insistir in ("null", "", "none", 0):
                insistir = None
            grupo = a.get("grupo")
            if grupo in ("null", "", "none"):
                grupo = None
            if not _cuando_ok(cuando):
                lineas.append(f"🤔 No cree el recordatorio <i>{esc(txt)}</i>: la IA dio una fecha/hora invalida.")
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
            db.add_recordatorio(cuando, txt, rep, insistir, grupo)
            if grupo:
                if grupo not in grupos_vistos:
                    grupos_vistos.add(grupo)
                    lineas.append("📋 <b>Plan de avisos creado</b> (marca Hecho en cualquiera y se apagan todos):")
                lineas.append(f"  🕐 {esc(cuando.replace('T',' · '))} — <i>{esc(txt)}</i>")
                continue
            if insistir:
                extra = f"\n      🔔 te insistire cada {esc(str(insistir))} min hasta que lo marques hecho"
            elif rep:
                extra = f"\n      🔁 se repite {esc(rep)}"
            else:
                extra = ""
            lineas.append(f"⏰ Recordatorio: <i>{esc(txt)}</i>\n      🕐 {esc(cuando.replace('T',' · '))}{extra}")
        elif tipo == "borrar_recordatorio":
            q = db.borrar_recordatorio(a.get("objetivo", ""))
            if q:
                msg = f"✅ Recordatorio apagado: <i>{esc(q['texto'])}</i>"
                if q.get("borrados", 0) > 1:
                    msg += f" (y sus {q['borrados']} avisos 🔕)"
                lineas.append(msg)
            else:
                lineas.append("🤔 No encontre ese recordatorio.")
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
                    msg += f"\n      🔥 Racha: {r} dias seguidos avanzando!"
                msg += f"\n      ▶️ Sigue: <i>{esc(sig['titulo'])}</i>" if sig else "\n      🎉 <b>Proyecto terminado!</b>"
                lineas.append(msg)
            else:
                lineas.append("🤔 No encontre fases pendientes en ese proyecto.")
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
                lineas.append("🤔 No encontre notas con eso.")
        elif tipo == "borrar_nota":
            q = db.borrar_nota(a.get("objetivo", ""))
            lineas.append(f"🗑 Nota borrada: <i>{esc(q['texto'])}</i>" if q else "🤔 No encontre esa nota.")
        elif tipo == "agregar_interes":
            txt = (a.get("texto") or "").strip()
            if txt:
                db.add_interes(txt)
                lineas.append(f"🎯 Interes guardado: <i>{esc(txt)}</i>")
        elif tipo == "borrar_interes":
            q = db.borrar_interes(a.get("objetivo", ""))
            lineas.append(f"🗑 Interes borrado: <i>{esc(q)}</i>" if q else "🤔 No encontre ese interes.")
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
    return lineas, cambio


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


def vigilar_recordatorios(token, cfg, parar):
    """Envia los recordatorios vencidos (de CUALQUIER usuario, cada uno a su
    chat) y duerme justo hasta el proximo. Si llega un mensaje, se reevalua."""
    # Este hilo trabaja por defecto sobre el dueño principal (respaldo,
    # sugerencias). Para los recordatorios usa el dueño de cada fila.
    db.set_dueno(db.DUENO_PRINCIPAL)
    chat_ids = chat_ids_permitidos(cfg)
    while not parar.is_set():
        espera = 300  # tope: 5 min
        try:
            db.respaldo_diario()
            for r in db.recordatorios_vencidos():  # de todos los dueños
                botones = [[
                    {"text": "✅ Hecho", "callback_data": f"rec_done:{r['id']}"},
                    {"text": "⏰ +30 min", "callback_data": f"rec_post:{r['id']}"},
                ]]
                for cid in destinos_de(r.get("dueno") or db.DUENO_PRINCIPAL, cfg):
                    A.enviar_mensaje(f"⏰ <b>Recordatorio:</b> {esc(r['texto'])}",
                                     token, cid, botones=botones)
                db.marcar_enviado(r)
            sugerencia_proactiva(token, chat_ids)
            # Salud de la maquina: si algo esta critico, avisa (max 1 vez/hora)
            try:
                problemas = sistema.alertas()
                ult = float(db.estado_get("ult_alerta_sistema", 0) or 0)
                if problemas and time.time() - ult > 3600:
                    msg = "⚠️ <b>Alerta de la maquina</b>\n" + "\n".join(
                        f"  {p}" for p in problemas)
                    for cid in chat_ids:
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


# ----------------------------------------------------------- procesar mensaje
def manejar_mensaje(texto, cfg, token, chat_id, prefijo=""):
    tareas = db.cargar_tareas()

    # 0) Atajos con botones (menu minimalista).
    low = texto.strip().lower()
    if low in ("menu", "/menu", "m"):
        A.enviar_mensaje("👇 <b>¿Qué hacemos?</b>", token, chat_id,
                         botones=botones_menu())
        return
    if low in ("proyectos", "/proyectos", "mis proyectos"):
        A.enviar_mensaje(texto_proyectos(), token, chat_id,
                         botones=botones_proyectos())
        return
    if low in ("intereses", "/intereses", "mis intereses"):
        A.enviar_mensaje(texto_intereses(), token, chat_id)
        return

    # 0.5) Busqueda web fiable: "busca X" / "investiga X"
    m = re.match(r"^/?(?:busca(?:me|r)?|investiga)\s+(.+)$", texto.strip(), re.I)
    if m:
        consulta = m.group(1).strip(" ?¿!.")
        resultados = busqueda.buscar(consulta)
        if not resultados:
            A.enviar_mensaje("🔍 No encontre nada en la web sobre eso.", token, chat_id)
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
        lineas = ["🔍 <b>Encontre esto:</b>"]
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
            acciones, frase = gemini_ia.interpretar(
                texto, tareas, api_key, proyectos=proyectos,
                historial=historial, extras=extras, cfg=cfg,
                usar_gemini=time.time() >= pausa)
            lineas, cambio = ejecutar_acciones(acciones, tareas)
            respuesta = frase + (("\n\n" + "\n".join(lineas)) if lineas else "")
            if cambio:
                db.guardar_tareas(tareas)
            db.historial_add(chat_id, "user", texto)
            db.historial_add(chat_id, "model", respuesta)
            A.enviar_mensaje(prefijo + respuesta, token, chat_id)
            return
        except Exception as e:
            # Llega aqui solo si Gemini Y TODOS los respaldos fallaron.
            log.error("Todas las IAs fallaron: %s", e)
            A.enviar_mensaje(
                prefijo + "😴 Ninguna IA respondio (red o cuotas); "
                "intenta de nuevo en un rato.\n"
                "Mientras, entiendo comandos directos:\n"
                "  ⏰ <i>recuerdame X manana a las 10</i>\n"
                "  📅 <i>agendame X manana 10am</i>\n"
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
                    aviso += f"\n      🔥 Racha: {r} dias seguidos!"
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
                    aviso += f"\n🔕 Apague los {q['borrados']} avisos de esa tarea."
            else:
                aviso = "🤷 Ese recordatorio ya no existe."
        elif accion == "rec_post":
            nuevo = db.posponer_recordatorio(int(rid), 30)
            aviso = f"⏰ Pospuesto a las <b>{nuevo.split('T')[1]}</b>"
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
def main():
    # journald ya pone su timestamp; aqui solo nivel y nombre. LOG_LEVEL configurable.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    cfg, token, _ = A.cargar_config()
    chat_ids = chat_ids_permitidos(cfg)
    if not token or not chat_ids:
        log.error("Falta token o chat_id en config.json.")
        return
    log.info("Cuentas permitidas: %s", ", ".join(chat_ids))
    notificar_actualizacion(token, chat_ids)  # avisa por Telegram si hubo update

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

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                db.estado_set("tg_offset", offset)

                # Botones pulsados (✅ Hecho / ⏰ +30 min)
                cb = upd.get("callback_query")
                if cb:
                    emisor = str(cb["message"]["chat"]["id"])
                    db.set_dueno(dueno_de(emisor, cfg))  # datos del usuario correcto
                    manejar_boton(cb, cfg, token, emisor)
                    DESPERTAR.set()
                    continue

                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                emisor = str(msg["chat"]["id"])
                dueno = dueno_de(emisor, cfg)
                db.set_dueno(dueno)  # aisla los datos de cada usuario

                if "text" in msg:
                    if dueno == db.DUENO_PRINCIPAL:
                        db.estado_set("ultima_actividad", time.time())
                    manejar_mensaje(msg["text"], cfg, token, emisor)
                    DESPERTAR.set()  # por si el mensaje creo/borro recordatorios

        except KeyboardInterrupt:
            apagar()
            break
        except Exception as e:
            log.warning("Error en getUpdates (reintento en %ss): %s", espera_error, e)
            time.sleep(espera_error)
            espera_error = min(espera_error * 2, 60)  # 3→6→12→…→60s


if __name__ == "__main__":
    main()
