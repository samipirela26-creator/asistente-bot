# -*- coding: utf-8 -*-
"""
Parser local de fechas en español para recordatorios SIN gastar IA.

parsear(texto, ahora) -> (cuando_iso, repetir, texto_recordatorio) o None.
Entiende cosas como:
  recuerdame llamar al banco mañana a las 10
  avisame en 2 horas sacar el pan
  recuerdame el viernes a las 7pm cobrarle a Luis
  recuerdame todos los dias a las 7am tomar la pastilla
Si no esta seguro, devuelve None y decide la IA.
"""

import re
import datetime

DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
        "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6}

TRIGGER = re.compile(
    r"^(?:recuerdame|recuérdame|recordarme|avisame|avísame|"
    r"ponme\s+un\s+recordatorio(?:\s+(?:de|para))?)\s+(?:que\s+)?(.+)$",
    re.I | re.S)


def _quitar(texto, m):
    return (texto[:m.start()] + " " + texto[m.end():]).strip()


def _hora(texto):
    """Busca 'a las 7', 'a la 1:30 pm', '7am'... -> (HH:MM, texto_sin_eso)."""
    m = re.search(
        r"\ba\s+(?:las?|la)\s+(\d{1,2})(?::(\d{2}))?\s*"
        r"(am|pm|de\s+la\s+(?:mañana|manana|tarde|noche))?\b", texto, re.I)
    if not m:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", texto, re.I)
    if not m:
        if re.search(r"\bal?\s*mediod[ií]a\b", texto, re.I):
            mm = re.search(r"\bal?\s*mediod[ií]a\b", texto, re.I)
            return "12:00", _quitar(texto, mm)
        return None, texto
    h = int(m.group(1))
    mins = int(m.group(2) or 0)
    suf = (m.group(3) or "").lower()
    if ("pm" in suf or "tarde" in suf or "noche" in suf) and h < 12:
        h += 12
    if "am" in suf and h == 12:
        h = 0
    if not suf and h <= 7:
        # "a las 5" sin am/pm es ambiguo: que decida la IA
        return None, texto
    if h > 23 or mins > 59:
        return None, texto
    return f"{h:02d}:{mins:02d}", _quitar(texto, m)


def _fecha(resto, ahora):
    """Extrae la fecha (hoy/mañana/viernes/AAAA-MM-DD) -> (date|None, resto)."""
    mf = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", resto)
    if mf:
        try:
            return datetime.date.fromisoformat(mf.group(1)), _quitar(resto, mf)
        except ValueError:
            pass  # fecha imposible (ej. 2026-13-40): que decida la IA
    mm = re.search(r"\bpasado\s+ma[ñn]ana\b", resto, re.I)
    if mm:
        return ahora.date() + datetime.timedelta(days=2), _quitar(resto, mm)
    mm = re.search(r"\bma[ñn]ana\b", resto, re.I)
    if mm:
        return ahora.date() + datetime.timedelta(days=1), _quitar(resto, mm)
    mm = re.search(r"\bhoy\b", resto, re.I)
    if mm:
        return ahora.date(), _quitar(resto, mm)
    md = re.search(r"\b(?:el\s+)?(" + "|".join(DIAS) + r")\b", resto, re.I)
    if md:
        objetivo = DIAS[md.group(1).lower()]
        delta = (objetivo - ahora.weekday()) % 7 or 7
        return ahora.date() + datetime.timedelta(days=delta), _quitar(resto, md)
    return None, resto


EVENTO = re.compile(
    r"^(?:agendame|agéndame|agenda(?:r)?|ponme\s+(?:una\s+)?cita\s+(?:de|para|con)?)\s+"
    r"(?:un[a]?\s+)?(.+)$", re.I | re.S)


def parsear_evento(texto, ahora=None):
    """'agendame dentista mañana 10am' -> (fecha, hora|None, titulo) o None."""
    if ahora is None:
        ahora = datetime.datetime.now()
    m = EVENTO.match(texto.strip())
    if not m:
        return None
    resto = m.group(1).strip()
    hhmm, resto = _hora(resto)
    fecha, resto = _fecha(resto, ahora)
    if fecha is None:
        return None  # un evento necesita fecha clara; si no, decide la IA
    titulo = re.sub(r"\s+", " ", resto).strip(" ,.;:-")
    if not titulo:
        return None
    return fecha.isoformat(), hhmm, titulo


def parsear(texto, ahora=None):
    if ahora is None:
        ahora = datetime.datetime.now()
    m = TRIGGER.match(texto.strip())
    if not m:
        return None
    resto = m.group(1).strip()

    # --- repeticion
    repetir = None
    dia_rep = ""
    mr = re.search(r"\b(?:todos\s+los\s+d[ií]as|cada\s+d[ií]a|diario|a\s+diario)\b",
                   resto, re.I)
    if mr:
        repetir = "diario"
        resto = _quitar(resto, mr)
    else:
        mr = re.search(r"\b(?:todas\s+las\s+semanas|cada\s+semana|semanal|"
                       r"todos\s+los\s+(lunes|martes|mi[eé]rcoles|jueves|viernes|"
                       r"s[aá]bados?|domingos?))\b", resto, re.I)
        if mr:
            repetir = "semanal"
            dia_rep = (mr.group(1) or "").rstrip("s")
            resto = _quitar(resto, mr)

    # --- "en N horas/minutos"
    me = re.search(r"\ben\s+(\d+|una?|media)\s*(horas?|minutos?|min)\b", resto, re.I)
    if me and not repetir:
        n = me.group(1).lower()
        unidad = me.group(2).lower()
        if n == "media":
            delta = datetime.timedelta(minutes=30)
        else:
            cant = 1 if n in ("un", "una") else int(n)
            delta = datetime.timedelta(
                hours=cant) if unidad.startswith("hora") else datetime.timedelta(minutes=cant)
        cuando = ahora + delta
        txt = _quitar(resto, me)
        if not txt:
            return None
        return cuando.strftime("%Y-%m-%dT%H:%M"), None, txt

    # --- hora explicita
    hhmm, resto = _hora(resto)

    # --- fecha
    fecha, resto = _fecha(resto, ahora)

    if repetir == "semanal" and fecha is None:
        if dia_rep and dia_rep.lower() in DIAS:
            objetivo = DIAS[dia_rep.lower()]
            delta = (objetivo - ahora.weekday()) % 7 or 7
            fecha = ahora.date() + datetime.timedelta(days=delta)

    if hhmm is None:
        return None  # sin hora clara: que decida la IA
    if fecha is None:
        if repetir:
            fecha = ahora.date()
            if f"{fecha}T{hhmm}" <= ahora.strftime("%Y-%m-%dT%H:%M"):
                fecha += datetime.timedelta(days=1)
        else:
            # solo hora: hoy si aun no pasa, si no mañana
            fecha = ahora.date()
            if f"{fecha}T{hhmm}" <= ahora.strftime("%Y-%m-%dT%H:%M"):
                fecha += datetime.timedelta(days=1)

    resto = re.sub(r"\s+", " ", resto).strip(" ,.;:-")
    if not resto:
        return None
    return f"{fecha.isoformat()}T{hhmm}", repetir, resto
