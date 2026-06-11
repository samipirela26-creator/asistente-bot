# -*- coding: utf-8 -*-
"""
Busqueda web fiable SIN librerias externas (solo stdlib).

Combina dos fuentes gratuitas:
  1. Wikipedia en español (API oficial) -> datos verificados
  2. DuckDuckGo (pagina HTML)           -> actualidad / web general

buscar(consulta) -> lista de dicts {"titulo", "resumen", "url", "fuente"}
La IA del bot redacta la respuesta SOLO con estas fuentes y las cita.
"""

import json
import re
import html
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("agenda.busqueda")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _wikipedia(consulta, n=2):
    """Busca en Wikipedia ES y trae el extracto de los mejores articulos."""
    out = []
    try:
        q = urllib.parse.quote(consulta)
        data = json.loads(_get(
            "https://es.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&srlimit={n}&format=json&utf8=1"))
        for it in data.get("query", {}).get("search", []):
            titulo = it["title"]
            try:
                ext = json.loads(_get(
                    "https://es.wikipedia.org/api/rest_v1/page/summary/"
                    + urllib.parse.quote(titulo)))
                resumen = ext.get("extract", "")[:600]
            except Exception:
                resumen = re.sub(r"<[^>]+>", "", it.get("snippet", ""))
            if resumen:
                out.append({
                    "titulo": titulo,
                    "resumen": resumen,
                    "url": "https://es.wikipedia.org/wiki/"
                           + urllib.parse.quote(titulo.replace(" ", "_")),
                    "fuente": "Wikipedia",
                })
    except Exception as e:
        log.warning("Wikipedia no respondio: %s", e)
    return out


def _duckduckgo(consulta, n=4):
    """Scrapea la version HTML simple de DuckDuckGo (sin API key)."""
    out = []
    try:
        q = urllib.parse.quote(consulta)
        pagina = _get(f"https://html.duckduckgo.com/html/?q={q}&kl=es-es")
        limpiar = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        # Patron principal: titulo + snippet (maquetado clasico de DDG).
        bloques = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?class="result__snippet"[^>]*>(.*?)</a>',
            pagina, re.S)
        for href, titulo, snip in bloques[:n]:
            # DDG envuelve la URL real en un redirect uddg=
            m = re.search(r"uddg=([^&]+)", href)
            url = urllib.parse.unquote(m.group(1)) if m else href
            out.append({
                "titulo": limpiar(titulo)[:120],
                "resumen": limpiar(snip)[:400],
                "url": url,
                "fuente": "DuckDuckGo",
            })
        # Respaldo: si cambian el HTML y el patron principal no halla nada,
        # rescatamos al menos los enlaces de resultado (sin snippet).
        if not out:
            for href, titulo in re.findall(
                    r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    pagina, re.S)[:n]:
                m = re.search(r"uddg=([^&]+)", href)
                url = urllib.parse.unquote(m.group(1)) if m else href
                titulo_limpio = limpiar(titulo)
                if titulo_limpio:
                    out.append({
                        "titulo": titulo_limpio[:120],
                        "resumen": "",
                        "url": url,
                        "fuente": "DuckDuckGo",
                    })
    except Exception as e:
        log.warning("DuckDuckGo no respondio: %s", e)
    return out


def buscar(consulta):
    """Wikipedia + DuckDuckGo. Devuelve hasta ~6 resultados."""
    return _wikipedia(consulta) + _duckduckgo(consulta)


if __name__ == "__main__":
    import sys
    for r in buscar(" ".join(sys.argv[1:]) or "Maracaibo"):
        print(f"[{r['fuente']}] {r['titulo']}\n  {r['resumen'][:100]}\n  {r['url']}\n")
