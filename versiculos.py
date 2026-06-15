"""Versículos para Larry — los elige la MÁQUINA (no la IA).

Lee la carpeta `versiculos/` (archivos .txt editables a mano) y devuelve uno al
azar. Pensado para anexarse a los partes matutino y nocturno. Sin dependencias:
solo stdlib. Si la carpeta no existe o está vacía, devuelve None y el que llama
sigue como si nada (el versículo es un extra, nunca un requisito)."""
import logging
import os
import random

log = logging.getLogger("versiculos")

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "versiculos")


def _parsear(texto):
    """De un .txt con bloques (cita / comentario separados por línea en blanco)
    a una lista de (cita, comentario). Ignora líneas que empiezan con #."""
    versiculos = []
    bloque = []
    for linea in texto.splitlines():
        s = linea.strip()
        if s.startswith("#"):
            continue
        if not s:
            if bloque:
                versiculos.append(bloque)
                bloque = []
            continue
        bloque.append(s)
    if bloque:
        versiculos.append(bloque)

    out = []
    for b in versiculos:
        cita = b[0]
        comentario = " ".join(b[1:]) if len(b) > 1 else ""
        out.append((cita, comentario))
    return out


def cargar(directorio=None):
    """Lee TODOS los .txt de la carpeta y los junta. Relee del disco en cada
    llamada (son pocos KB), así editar el archivo surte efecto sin reiniciar."""
    d = directorio or _DIR
    versiculos = []
    try:
        archivos = sorted(f for f in os.listdir(d) if f.endswith(".txt"))
    except OSError as e:
        log.warning("No pude leer la carpeta de versículos (%s): %s", d, e)
        return []
    for nombre in archivos:
        try:
            with open(os.path.join(d, nombre), encoding="utf-8") as f:
                versiculos.extend(_parsear(f.read()))
        except OSError as e:
            log.warning("No pude leer %s: %s", nombre, e)
    return versiculos


def aleatorio(directorio=None):
    """(cita, comentario) al azar, o None si no hay ninguno."""
    versiculos = cargar(directorio)
    return random.choice(versiculos) if versiculos else None


def formato_html(par):
    """Da formato de Telegram (HTML) a un (cita, comentario) en voz de Larry.
    `par` puede ser None -> devuelve cadena vacía."""
    if not par:
        return ""
    cita, comentario = par
    from html import escape
    bloque = f"<b>{escape(cita)}</b>"
    if comentario:
        bloque += f"\n<i>{escape(comentario)}</i>"
    return bloque


def para_parte(directorio=None):
    """Atajo: un versículo al azar ya formateado para anexar a un parte.
    Cadena vacía si no hay ninguno (el que llama lo trata como opcional)."""
    return formato_html(aleatorio(directorio))
