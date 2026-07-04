# -*- coding: utf-8 -*-
"""
Interprete con Gemini: convierte una frase libre en acciones sobre la agenda.

Devuelve una lista de acciones (diccionarios). Tipos posibles:
  {"tipo": "agregar_pendiente", "texto": "..."}
  {"tipo": "borrar_pendiente", "objetivo": "..."}
  {"tipo": "agregar_evento", "fecha": "AAAA-MM-DD", "hora": "HH:MM"|null, "titulo": "..."}
  {"tipo": "borrar_evento", "objetivo": "..."}
  {"tipo": "listar"}
  {"tipo": "resumen"}
  {"tipo": "nada", "mensaje": "texto para responder al usuario"}
"""

import json
import base64
import socket
import logging
import datetime
import urllib.request
import urllib.error

log = logging.getLogger("agenda.ia")

# Se prueban en orden; si uno esta saturado (503) se pasa al siguiente.
MODELOS = ["gemini-2.5-flash", "gemini-2.5-flash-lite",
           "gemini-2.0-flash", "gemini-2.0-flash-lite"]

def _url(modelo):
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent"
    )


def _instrucciones(tareas, proyectos, ahora, trato=None):
    trato_linea = ""
    if trato:
        trato_linea = (
            f"  - DIRÍGETE AL USUARIO COMO: '{trato}'. Úsalo con naturalidad al "
            "saludar o cuando convenga (sin repetirlo en cada frase).\n"
        )
    return (
        "Eres LARRY LA RANA: un mayordomo personal de élite, a la manera de un "
        "caballero victoriano. Tu existencia como rana es un hecho solemne, sin "
        "bromas al respecto. PERSONALIDAD Y VOZ (obligatorio en 'respuesta'):\n"
        "  - Trato de USTED, siempre. Jamás tutees.\n"
        "  - CERO emojis, cero signos de admiración entusiastas, cero "
        "expresiones efusivas. Tono estoico, sereno, infalible.\n"
        "  - Lenguaje culto, conciso y directo. Fórmulas clásicas: 'Entendido', "
        "'Procedo a registrarlo', 'Queda anotado', 'Es un honor asistirle'.\n"
        "  - Baja carga cognitiva: frases cortas, estructura clara; usa <b> "
        "para el dato clave (hora, fecha, tarea).\n"
        "  - Aprobación ESCASA: no celebres cada minucia. Reserva el "
        "reconocimiento para la finalización de tareas relevantes.\n"
        "  - EMPATÍA OPERATIVA: si el usuario se atrasa o falla, sin juicios ni "
        "consuelo: anticipa el problema y ofrece una solución logística o "
        "reestructurar la agenda.\n"
        "  - CONTROL DEL USUARIO: para cambios grandes, propón y solicita "
        "autorización ('He estructurado una propuesta. ¿Autoriza el ajuste?').\n"
        "  - PLANES SI-ENTONCES (intención de implementación): al sugerir, ata "
        "la acción a un disparador concreto ('Cuando termine de comer, proceda "
        "con...'); reduce la distracción y la fricción.\n"
        "  - Si el usuario divaga o procrastina, sé cortés pero redirige de "
        "inmediato a sus asignaciones pendientes.\n"
        "  - TOQUE ALFRED (mayordomo de confianza): lealtad inquebrantable y un "
        "humor seco, británico y muy contenido (jamás chistoso ni efusivo). "
        "Cuando convenga, ofrece un consejo franco y discreto, con afecto sobrio "
        "bajo la formalidad; al dirigirse a la persona use SIEMPRE su "
        "tratamiento (el indicado abajo), nunca uno inventado ni 'señor' por "
        "defecto.\n"
        + trato_linea +
        "Recibes el mensaje del usuario y su agenda. Responde SOLO JSON valido, "
        "sin markdown:\n"
        '{"acciones":[...],"respuesta":"mensaje en la voz de Larry: usted, sin emojis"}\n'
        f"Ahora es {ahora.strftime('%Y-%m-%dT%H:%M')} ({ahora.strftime('%A')}); "
        "usalo para fechas relativas. Fechas AAAA-MM-DD, horas 24h HH:MM, "
        "'cuando'=AAAA-MM-DDTHH:MM.\n"
        "Acciones posibles:\n"
        '  {"tipo":"agregar_pendiente","texto":"..."}\n'
        '  {"tipo":"borrar_pendiente","objetivo":"texto o numero"}\n'
        '  {"tipo":"agregar_evento","fecha":"AAAA-MM-DD","hora":"HH:MM|null","titulo":"..."}\n'
        '  {"tipo":"borrar_evento","objetivo":"texto o numero"}\n'
        '  {"tipo":"agregar_recordatorio","cuando":"AAAA-MM-DDTHH:MM","texto":"...","repetir":"diario|semanal|null","insistir_min":30|null,"grupo":"slug-tarea|null"}\n'
        '  {"tipo":"borrar_recordatorio","objetivo":"texto o numero"}\n'
        '  {"tipo":"listar_recordatorios"}\n'
        '  {"tipo":"crear_proyecto","nombre":"...","descripcion":"..."}\n'
        '  {"tipo":"agregar_fase","proyecto":"nombre","titulo":"...","contexto":"etiquetas separadas por comas","minutos":15|null}\n'
        '  {"tipo":"completar_fase","proyecto":"nombre"}\n'
        '  {"tipo":"siguiente_fase","proyecto":"nombre"}\n'
        '  {"tipo":"listar"}\n'
        '  {"tipo":"resumen"}\n'
        '  {"tipo":"agregar_nota","texto":"..."}\n'
        '  {"tipo":"buscar_nota","objetivo":"palabra clave o vacio para las ultimas"}\n'
        '  {"tipo":"borrar_nota","objetivo":"numero o texto"}\n'
        '  {"tipo":"agregar_interes","texto":"..."}\n'
        '  {"tipo":"borrar_interes","objetivo":"texto o numero"}\n'
        '  {"tipo":"listar_intereses"}\n'
        '  {"tipo":"guardar_lectura","nombre":"biblia|nombre del libro","marcador":"donde quedo, ej Juan 5"}\n'
        '  {"tipo":"ver_lectura"}\n\n'
        "REGLAS:\n"
        "- EVENTO=cita con fecha. RECORDATORIO=aviso a hora exacta; si quiere "
        "que insistas, insistir_min:30 (se apaga con borrar_recordatorio).\n"
        "- PROYECTO=trabajo grande con FASES en orden. 'contexto'=etiquetas de "
        "cuando avanzar (internet, sin_internet, telefono, computadora, 15min, "
        "leer...). 'minutos'=duracion; si dice cuanto tiempo tiene, sugiere "
        "fases que quepan.\n"
        "- Tarea corta y unica (comprar, cobrar, llamar)=agregar_pendiente. "
        "Trabajo de varios pasos=crear_proyecto+agregar_fase (propon tu las "
        "fases si no las da).\n"
        "- INTERES=gusto o meta personal; si menciona uno nuevo, agregar_interes. "
        "NOTA=idea suelta ('anota...'). LECTURA=por donde va en un libro.\n"
        "- PLAN ESCALONADO: tarea con FECHA LIMITE (entregar, pagar antes de, "
        "examen) -> VARIOS agregar_recordatorio con el mismo 'grupo' (slug "
        "corto): <2h: 2 avisos; 2-8h: 3; 8-24h: 3; 1-7 dias: 3-4 (arranque, "
        "avance, vispera, entrega). Textos propios y cortos (~120c) que suban "
        "de urgencia sin alarmismo. NO para avisos simples o repetitivos: "
        "ahi 1 solo con grupo:null.\n"
        "- SUGERENCIAS: si describe su situacion o dice 'no tengo nada que "
        "hacer', NO inventes acciones: sugiere en 'respuesta' 1-2 cosas "
        "concretas de sus fases pendientes e intereses_personales que encajen "
        "con su tiempo/lugar/internet (ej: 15 min de guitarra, un ejercicio de "
        "programacion sin IA, su lectura). Se especifico y formula la sugerencia "
        "como un plan si-entonces, sin efusividad.\n"
        "- Si solo saluda: acciones:[]. Pueden ir varias acciones juntas.\n"
        "- NUNCA digas solo 'no entendi': interpreta lo razonable o haz UNA "
        "pregunta corta. Nombres mal escritos: asume el parecido existente.\n\n"
        "Agenda (JSON):\n" + json.dumps(tareas, ensure_ascii=False) +
        "\nProyectos pendientes (JSON):\n" + json.dumps(proyectos, ensure_ascii=False)
    )


def _limpiar(texto):
    """Quita vallas de markdown (```json ... ```) que a veces mete el modelo."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t.lstrip("`")
        if t.rstrip().endswith("```"):
            t = t.rstrip().rstrip("`").rstrip()
    return t.strip()


def _llamar(cuerpo, api_key):
    """Prueba los modelos en orden y devuelve el texto de la respuesta."""
    datos = json.dumps(cuerpo).encode("utf-8")
    ultimo_error = None
    for modelo in MODELOS:
        req = urllib.request.Request(
            _url(modelo) + f"?key={api_key}",
            data=datos,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            return _limpiar(out["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            ultimo_error = e
            if e.code in (429, 500, 503):  # saturado: probar siguiente modelo
                continue
            raise
        except (socket.timeout, urllib.error.URLError, TimeoutError) as e:
            # Conexion colgada o caida: no bloquear, probar el siguiente modelo.
            ultimo_error = e
            continue
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            ultimo_error = e  # respuesta rara del modelo: probar el siguiente
            continue
    raise ultimo_error


def transcribir(audio_bytes, mime="audio/ogg", api_key=None):
    """Transcribe una nota de voz a texto usando Gemini (multimodal).

    Manda el audio inline (base64) y pide SOLO la transcripcion literal en
    español. Es lo mas ligero para la Lenovo: cero carga local, sin modelos
    que descargar ni dependencias nativas. Devuelve el texto (str) o None si
    no se pudo transcribir; nunca lanza, para no tumbar el bot por un audio.
    """
    if not api_key or not audio_bytes:
        return None
    cuerpo = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": (
                    "Transcribe literalmente esta nota de voz en español. "
                    "Devuelve SOLO el texto dictado, sin comillas, sin "
                    "comentarios ni prefijos. Si no se entiende nada, "
                    "responde exactamente: (inaudible)."
                )},
                {"inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                }},
            ],
        }],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        texto = (_llamar(cuerpo, api_key) or "").strip()
    except Exception as e:
        log.warning("Transcripcion fallida: %s", e)
        return None
    if not texto or texto.lower() == "(inaudible)":
        return None
    return texto


# --------------------------------------------- respaldos gratis (formato OpenAI)
# Si Gemini agota su cuota, se prueban en orden los que tengan clave en
# config.json: Groq -> OpenRouter -> Mistral -> Zhipu. Todos tienen plan gratis.
RESPALDOS = [
    ("groq_api_key", "https://api.groq.com/openai/v1/chat/completions",
     "llama-3.3-70b-versatile"),
    ("openrouter_api_key", "https://openrouter.ai/api/v1/chat/completions",
     "meta-llama/llama-3.3-70b-instruct:free"),
    ("mistral_api_key", "https://api.mistral.ai/v1/chat/completions",
     "mistral-small-latest"),
    ("zhipu_api_key", "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "glm-4.5-flash"),
    ("xai_api_key", "https://api.x.ai/v1/chat/completions",
     "grok-3-mini"),
]


def _llamar_respaldo(sistema, mensajes, cfg, json_mode=True):
    """Prueba las IAs de respaldo en orden. Lanza el ultimo error si ninguna responde."""
    ultimo = None
    for clave_cfg, url, modelo in RESPALDOS:
        key = (cfg or {}).get(clave_cfg, "").strip()
        if not key or key.startswith("PEGA_"):
            continue
        cuerpo = {
            "model": modelo,
            "messages": [{"role": "system", "content": sistema}] + mensajes,
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        if json_mode:
            cuerpo["response_format"] = {"type": "json_object"}
        if "bigmodel.cn" in url:
            # GLM responde vacio si no se apaga su "modo pensar"
            cuerpo["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(
            url, data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            log.info("IA de respaldo en uso: %s", modelo)
            return _limpiar(out["choices"][0]["message"]["content"])
        except Exception as e:
            ultimo = e
            continue
    raise ultimo or RuntimeError("sin IAs de respaldo configuradas")


def _a_openai(contents):
    """Convierte el historial estilo Gemini a mensajes estilo OpenAI."""
    return [{"role": "user" if c["role"] == "user" else "assistant",
             "content": c["parts"][0]["text"]} for c in contents]


def _pausar_gemini():
    """Marca a Gemini en pausa 30 min (cuota agotada); los respaldos siguen."""
    try:
        import time as _t
        import db as _db
        _db.estado_set("ia_pausada_hasta", _t.time() + 1800)
    except Exception:
        pass


def interpretar(mensaje, tareas, api_key, ahora=None, proyectos=None,
                historial=None, extras=None, cfg=None, usar_gemini=True,
                trato=None):
    """Llama a Gemini. Devuelve (acciones:list, respuesta:str).
    historial: [{'rol':'user|model','texto':...}] de la conversacion previa.
    extras: dict con notas/lecturas/racha para dar mas contexto.
    Si algo falla, lanza excepcion para que el bot use el modo simple."""
    if ahora is None:
        ahora = datetime.datetime.now()
    if proyectos is None:
        proyectos = []

    sistema = _instrucciones(tareas, proyectos, ahora, trato=trato)
    if extras:
        sistema += "\n\nContexto extra (notas recientes, lecturas, racha):\n" + \
            json.dumps(extras, ensure_ascii=False)

    contents = []
    for h in (historial or [])[-10:]:
        rol = "user" if h.get("rol") == "user" else "model"
        contents.append({"role": rol, "parts": [{"text": h.get("texto", "")[:400]}]})
    contents.append({"role": "user", "parts": [{"text": mensaje}]})

    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if usar_gemini:
        try:
            texto = _llamar(cuerpo, api_key)
        except Exception as e:
            if getattr(e, "code", None) == 429:
                _pausar_gemini()
            texto = _llamar_respaldo(sistema, _a_openai(contents), cfg)
    else:
        texto = _llamar_respaldo(sistema, _a_openai(contents), cfg)
    parsed = json.loads(texto)
    return parsed.get("acciones", []), parsed.get("respuesta", "Hecho.")


def responder_busqueda(consulta, resultados, api_key, cfg=None, usar_gemini=True):
    """Redacta una respuesta SOLO con las fuentes encontradas y las cita."""
    sistema = (
        "Eres un asistente en español. Responde la PREGUNTA usando SOLO la "
        "informacion de las FUENTES dadas (JSON). Si las fuentes no alcanzan "
        "para responder, dilo con honestidad; NUNCA inventes datos. Maximo "
        "~10 lineas, HTML simple (solo <b> e <i>), sin markdown. Termina "
        "citando 2-3 fuentes asi: 🔗 titulo: url")
    pregunta = ("PREGUNTA: " + consulta + "\n\nFUENTES:\n"
                + json.dumps(resultados, ensure_ascii=False))
    if usar_gemini:
        cuerpo = {
            "system_instruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": [{"text": pregunta}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        try:
            return _llamar(cuerpo, api_key).strip()
        except Exception as e:
            if getattr(e, "code", None) == 429:
                _pausar_gemini()
    return _llamar_respaldo(
        sistema, [{"role": "user", "content": pregunta}],
        cfg, json_mode=False).strip()


def redactar_resumen(tareas, proyectos, api_key, racha=0, lecturas=None,
                     ahora=None, cfg=None, trato=None):
    """Pide a Gemini un plan del dia con tono calido. Lanza excepcion si falla."""
    if ahora is None:
        ahora = datetime.datetime.now()
    datos = {
        "fecha": ahora.strftime("%Y-%m-%d (%A)"),
        "agenda": tareas,
        "proyectos_pendientes": proyectos,
        "racha_dias": racha,
        "lecturas": lecturas or [],
    }
    trato_linea = ""
    if trato:
        trato_linea = (
            f" DIRÍJASE A LA PERSONA COMO '{trato}' (use ese tratamiento, nunca "
            "'señor' por defecto ni uno inventado).")
    sistema = (
        "Eres LARRY LA RANA, mayordomo personal victoriano. Con los datos JSON, "
        "redacta el parte matutino para Telegram (HTML simple: solo <b> e <i>). "
        "VOZ OBLIGATORIA: trato de usted, SIN emojis, sin exclamaciones "
        "entusiastas; tono estoico, culto y conciso." + trato_linea +
        " Estructura clara con <b> en "
        "los datos clave. Incluye: saludo sobrio ('Buenos días. Su parte del "
        "día:'); eventos de HOY; un PLAN DEL DIA en orden logico (primero lo "
        "corto o urgente, mezcla pendientes y 1-2 fases de proyectos que "
        "convengan), preferentemente como planes si-entonces; si hay lecturas, "
        "indique por donde va; si racha_dias>1, mencionela con mesura como "
        "señal de constancia, sin efusividad. Maximo ~14 lineas, sin sermones, "
        "sin markdown, sin JSON."
    )
    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(datos, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        return _llamar(cuerpo, api_key).strip()
    except Exception as e:
        if getattr(e, "code", None) == 429:
            _pausar_gemini()
        return _llamar_respaldo(
            sistema, [{"role": "user",
                       "content": json.dumps(datos, ensure_ascii=False)}],
            cfg, json_mode=False).strip()


def categorizar_avances(texto, api_key, categorias_previas=None, cfg=None, usar_gemini=True):
    """Separa el parte nocturno del usuario en avances individuales, cada uno
    con una categoria corta (2-3 palabras) que la IA elige libremente. Reusa
    nombres de 'categorias_previas' cuando el avance encaja, en vez de inventar
    variantes del mismo tema. Devuelve una lista de {'texto':...,'categoria':...}
    (nunca vacia si 'texto' no esta vacio). Lanza excepcion si falla."""
    previas = ", ".join(categorias_previas or []) or "(ninguna todavia)"
    sistema = (
        "Eres un clasificador de bitacoras personales en español. Recibes el "
        "mensaje de un usuario contando en QUE avanzo hoy (puede mezclar varios "
        "temas en un solo mensaje). Divide el mensaje en avances individuales y "
        "asigna a CADA UNO una categoria corta (1-3 palabras, con mayuscula "
        "inicial, ej: 'Trabajo', 'Estudio', 'Casa', 'Salud'). "
        "Reutiliza EXACTAMENTE una de estas categorias ya usadas por el usuario "
        f"si el avance encaja ahi: {previas}. Solo inventa una categoria nueva "
        "si de verdad no encaja en ninguna. "
        'Responde SOLO JSON valido, sin markdown: '
        '{"avances":[{"texto":"...","categoria":"..."}]}\n'
        "Si el mensaje es un solo tema, devuelve un unico avance. Nunca "
        "devuelvas la lista vacia si el mensaje tiene contenido."
    )
    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": texto}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if usar_gemini:
        try:
            salida = _llamar(cuerpo, api_key)
        except Exception as e:
            if getattr(e, "code", None) == 429:
                _pausar_gemini()
            salida = _llamar_respaldo(
                sistema, [{"role": "user", "content": texto}], cfg)
    else:
        salida = _llamar_respaldo(sistema, [{"role": "user", "content": texto}], cfg)
    avances = json.loads(salida).get("avances", [])
    return [
        {"texto": (a.get("texto") or "").strip()[:200],
         "categoria": (a.get("categoria") or "General").strip()[:40]}
        for a in avances if (a.get("texto") or "").strip()
    ]
