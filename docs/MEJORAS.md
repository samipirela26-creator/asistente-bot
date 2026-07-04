# Mejoras propuestas para el asistente

> Documento de investigación nocturna. **NADA aquí está aplicado al código todavía.**
> Solo son propuestas con código de referencia para que tú decidas qué implementar
> (manualmente o con Ralph). Cada propuesta indica: problema, evidencia/fuente,
> y un boceto de cómo se haría. Las propuestas se marcan con prioridad sugerida.

Última actualización: 2026-06-11 06:26 (CIERRE · investigación nocturna terminada)

---

## Resumen del estado actual (lo que ya está BIEN hecho)

Antes de proponer, conviene reconocer lo que el código ya hace correctamente, para
no "arreglar" lo que no está roto:

- **SQLite con WAL** ya configurado en `db.py` (`conn()`): `journal_mode=WAL`,
  `busy_timeout=10000`, `synchronous=NORMAL`. Esto es exactamente lo que recomiendan
  las guías de producción 2026.
- **Respaldo diario** con rotación (conserva 7) en `db.respaldo_diario()`.
- **Índices** ya creados para las consultas frecuentes (`idx_rec_pend`, `idx_fases_pend`).
- **Migraciones suaves** con `PRAGMA table_info` + `ALTER TABLE ... IF NOT EXISTS` lógico.
- **Cascada de IA con respaldos** (Gemini → Groq → OpenRouter → Mistral → Zhipu → xAI):
  excelente para no quedarse sin servicio por cuota.
- **Parser por reglas antes de la IA** (`procesar_simple`, `atajo`): ahorra cuota
  gratis para comandos claros. Muy buen patrón.
- **Long polling con timeout de 50s** en `bot.py` (no es polling agresivo).

---

## ⭐ QUICK WINS — resumen ejecutivo para la mañana

Si solo tienes tiempo para 3 cosas, haz estas (máximo impacto, mínimo riesgo):

1. **Crear `.gitignore` + rotar secretos (SEC-1 / 4.1).** Es lo más urgente: el repo no
   tiene `.gitignore` y `config.json` guarda token de Telegram y 5 API keys.
   El propio `LEEME.md` (líneas 109-110) YA anota como pendiente "regenerar el token y la
   API key porque se compartieron en texto" → **hazlo de verdad**: `/revoke` en BotFather
   y regenera las keys en aistudio.google.com, y crea el `.gitignore`. 10 minutos, evita
   una fuga seria. Riesgo: nulo.

2. **Persistir el `offset` de Telegram (BOT-1 / 1.1).** 2 líneas usando la tabla `estado`
   que ya existe. Evita que al reiniciar el bot se reprocesen o pierdan mensajes. Riesgo: bajo.

3. **Suite de tests de `fechas.py` (TEST-1 / 4.3).** El parser de fechas en español es
   lógica pura y crítica (sin él, mal-interpreta recordatorios). Tener tests permite
   refactorizar después con red de seguridad. El boceto ya está listo abajo. Riesgo: nulo
   (solo añade archivos en `tests/`, no toca el código existente).

**Bonus (BUG, hazlo también):** **escapar HTML del texto dinámico (9.1).** Si escribes
un pendiente con `&` o `<` (ej: "comprar pan & leche"), Telegram rechaza el mensaje. Es
un fallo real, no estético, y el arreglo es pequeño (`html.escape`). Ver enfoque 9.

> Todo lo demás (logging, split de bot.py, responseSchema, concurrencia) es mejora
> incremental: válido, pero no empieces por ahí.

---

## ENFOQUE 1 — Bot de Telegram

### 1.1 [PRIORIDAD ALTA] El `offset` se pierde si el bot se reinicia
**Problema:** en `bot.py:833`, `offset = None` vive solo en memoria. Si el proceso
se reinicia (crash, reboot, systemd restart), Telegram reenvía updates ya procesados
o el bot pierde el hilo. Las guías 2026 recomiendan persistir el offset.

**Evidencia:** "Store the offset in persistent storage, not memory... write the offset
to a file, Redis key, or database row, never rely on a Python variable."

**Boceto (usa la tabla `estado` que YA existe):**
```python
# al arrancar main():
offset = int(db.estado_get("tg_offset", 0) or 0) or None
# tras procesar cada update:
offset = upd["update_id"] + 1
db.estado_set("tg_offset", offset)
```
Coste: ~2 líneas. Riesgo: bajo. Reutiliza `estado_get/estado_set`.

### 1.2 [PRIORIDAD MEDIA] Sin backoff exponencial ante errores de Telegram
**Problema:** en `bot.py:872-874`, ante cualquier excepción se hace `time.sleep(3)`
fijo. Si Telegram devuelve 429 (rate limit) o cae la red un rato, reintentar cada
3s puede empeorar el rate-limit.

**Evidencia:** "Implement exponential backoff... wait 1s, then 2, then 4, capping at 60s."

**Boceto:**
```python
espera_err = 3
while True:
    try:
        ...
        espera_err = 3  # éxito: resetea
    except KeyboardInterrupt:
        ...
    except Exception as e:
        print("Aviso (reintentando):", e)
        time.sleep(espera_err)
        espera_err = min(espera_err * 2, 60)
```

### 1.3 [PRIORIDAD BAJA / OPINIÓN] Migrar a `python-telegram-bot` o `pyTelegramBotAPI`
**Problema:** el bot usa `urllib` crudo. Funciona, pero una librería mantenida da
async, manejo de errores, rate-limit y reconexión "gratis".
**Contra:** es una reescritura grande; el código actual es legible y sin dependencias.
**Recomendación:** NO hacerlo salvo que quieras crecer mucho. El enfoque sin
dependencias es una virtud para un proyecto personal. Anotado como opción, no como deuda.

### 1.4 [PRIORIDAD MEDIA] El procesamiento bloquea el bucle de polling
**Problema:** `manejar_mensaje` (que puede llamar a Gemini con timeout de 40s) corre
dentro del bucle de `getUpdates`. Si la IA tarda, el bot no lee mensajes nuevos mientras tanto.
**Evidencia 2026:** "don't handle messages inside the polling loop... process in separate workers."
**Boceto ligero (sin Redis, solo threads):** despachar cada update a un
`threading.Thread` o un `concurrent.futures.ThreadPoolExecutor(max_workers=4)`.
Cuidado: `db.py` abre una conexión por llamada, así que es razonablemente seguro,
pero `tareas` (cargar/guardar) podría competir entre hilos del mismo chat. Conviene
serializar por `chat_id`. **Requiere diseño cuidadoso → describir bien antes de tocar.**

---

## ENFOQUE 2 — Integración IA (Gemini)

### 2.1 [PRIORIDAD MEDIA] Usar `responseSchema` en vez de describir el JSON en el prompt
**Problema:** en `gemini_ia.py:_instrucciones`, el esquema de acciones se describe a
mano dentro del prompt (decenas de líneas). Gemini 2.5 ya soporta JSON Schema nativo.
**Evidencia 2026:** "Do not duplicate schema structure in your prompt or provide example
JSON outputs, as this reduces output quality... use response_schema / response_json_schema."
**Beneficio:** salida garantizada sintácticamente válida, menos tokens, menos parsing frágil.
**Contra:** los modelos de respaldo (Groq, Mistral...) usan formato OpenAI y NO comparten
ese schema; habría que mantener dos caminos. Por eso es MEDIA, no ALTA.
**Boceto:** definir un `responseSchema` con `enum` para el campo `tipo`, y mover las
reglas de negocio (no la forma del JSON) al texto. Mantener el prompt-texto solo para
los respaldos OpenAI.

### 2.2 [PRIORIDAD BAJA] Validar semánticamente las acciones de la IA
**Problema:** `ejecutar_acciones` confía en los campos que manda la IA. Si la IA manda
una fecha mal formada o un `tipo` desconocido, simplemente se ignora (bien), pero
fechas/horas inválidas podrían colarse a la BD.
**Evidencia:** "structured output guarantees syntactically correct JSON... does NOT
guarantee values are semantically correct; always validate before using."
**Boceto:** una función `_validar_accion(a)` que verifique formato de `fecha`
(`AAAA-MM-DD`), `hora` (`HH:MM`) y `cuando` antes de insertar.

### 2.3 [PRIORIDAD BAJA] El contador de uso cuenta intentos, no éxitos
**Problema:** `db.uso_inc()` se llama ANTES de `gemini_ia.interpretar` (bot.py:711).
Si todo falla y cae a respaldos, igual cuenta como "uso de Gemini". El panel de
"uso ≈ 250/día" puede sobreestimar. Menor, pero afecta la decisión de pausar.

---

## ENFOQUE 3 — Base de datos / SQLite

### 3.1 [PRIORIDAD BAJA] Añadir PRAGMAs de rendimiento opcionales
La config actual ya es buena. Mejoras marginales que recomiendan las guías 2026:
```python
c.execute("PRAGMA cache_size=-16000")   # 16MB de caché (bot pequeño: no hace falta 64MB)
c.execute("PRAGMA temp_store=MEMORY")
c.execute("PRAGMA mmap_size=67108864")  # 64MB I/O mapeado
```
**Nota honesta:** para una BD de 80 KB y un solo usuario, esto NO cambia nada
perceptible. Es "por completitud", no por necesidad. Prioridad muy baja.

### 3.2 [PRIORIDAD MEDIA] `guardar_tareas` hace DELETE-ALL + INSERT-ALL
**Problema:** `db.guardar_tareas` borra TODA la tabla `pendientes` y `eventos` y la
reinserta en cada cambio. Funciona con pocos datos, pero pierde los `id` estables y
no escala. Con varios hilos (ver 1.4) es una posible carrera.
**Boceto alternativo:** operaciones puntuales `INSERT`/`DELETE WHERE id=?` en vez de
reescribir todo. Implica cambiar la firma (devolver ids), así que es un cambio mediano.
**Recomendación:** solo si se aborda la concurrencia (1.4). Si no, déjalo: es simple y correcto.

### 3.3 [PRIORIDAD BAJA] FTS5 para búsqueda de notas
`buscar_notas` usa `LIKE %x%`. Para muchas notas, FTS5 sería más rápido y con ranking.
Con 15 notas no importa. Anotado por si crece.

---

## ENFOQUE 4 — Arquitectura general

### 4.1 [PRIORIDAD ALTA — CONFIRMADO] Secretos en `config.json` sin `.gitignore`
**VERIFICADO en ciclo 2:** `asistente/` ES un repositorio git. **NO existe `.gitignore`**.
`config.json` (con `gemini_api_key`, `groq_api_key`, `openrouter_api_key`, `token` de
Telegram, etc.) ahora mismo NO está rastreado, pero al no haber `.gitignore`, un simple
`git add .` o `git add -A` lo subiría con TODAS las claves al historial. Además
`agenda.db` (datos personales) y `respaldos/` tampoco están ignorados.
**Evidencia 2026:** "Never commit secrets to version control... use .gitignore."
**Acción propuesta (ideal para Ralph, baja complejidad):**
1. Crear `asistente/.gitignore` con al menos:
   ```
   config.json
   agenda.db
   agenda.db-wal
   agenda.db-shm
   respaldos/
   __pycache__/
   *.pyc
   tareas.json
   ```
2. Añadir un `config.json.example` con las claves vacías como plantilla.
3. (Opcional) en `asistente.cargar_config`, permitir leer claves desde variables de
   entorno con fallback a config.json:
   ```python
   import os
   key = os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key", "")
   ```
**Nota de seguridad:** si en algún momento `config.json` llegó a commitearse, rotar
las claves; borrarlo del working tree no basta. (Verificado: hoy NO está en el árbol git.)

**ACTUALIZACIÓN ciclo 9 — más urgente de lo pensado:** `git ls-files` devuelve VACÍO:
el repositorio **no tiene NINGÚN archivo rastreado todavía**. Esto significa que el
primer `git add .` / `git add -A` que hagas añadiría de golpe `config.json` (token + 5
keys), `agenda.db` y `respaldos/*.db` (datos personales) al índice. Por eso el `.gitignore`
debe existir **ANTES del primer commit**. Contenido de `respaldos/` confirmado: copias
`agenda-AAAA-MM-DD.db` con tus datos → DEBEN ir ignoradas. `tareas.json` está vacío
(`{"eventos":[],"pendientes":[]}`), ya migrado a SQLite; ignorarlo también.

### 4.2 [PRIORIDAD MEDIA] `bot.py` tiene ~880 líneas con responsabilidades mezcladas
**Problema:** `bot.py` mezcla: formateo de texto (texto_*), botones, parser por reglas,
ejecución de acciones, hilo de recordatorios y el bucle principal.
**Evidencia 2026:** "once a file grows beyond ~300-400 lines... it's time to split it."
**Boceto de separación (sin cambiar lógica):**
- `vistas.py` → `texto_lista`, `texto_proyectos`, `texto_uso`, `botones_*`
- `acciones.py` → `ejecutar_acciones`, `procesar_simple`, `atajo`
- `recordatorios.py` → `vigilar_recordatorios`, `sugerencia_proactiva`
- `bot.py` → solo el bucle y el despacho
**Contra:** es refactor puro; riesgo de romper imports. Hacer con tests antes.

### 4.3 [PRIORIDAD ALTA] No hay tests automatizados
**Problema:** todo el parser por reglas (`fechas.parsear`, `procesar_simple`, regex)
es lógica pura y testeable, pero no hay tests. Un cambio puede romper el parseo
de fechas en español sin que nadie se entere.
**Boceto:** carpeta `tests/` con `pytest`:
- `test_fechas.py`: "mañana a las 10", "el viernes", "en 30 min" → ISO esperado.
- `test_procesar_simple.py`: comandos directos → acción esperada.
- `test_db.py`: usar BD temporal (`:memory:` o tmpfile) para CRUD.
Esto es lo de MAYOR retorno: permite refactorizar (4.2) con red de seguridad.
Es ideal para Ralph: cada test es una "user story" independiente y verificable.

---

## ENFOQUE 5 — Hallazgos por archivo (ciclo 2)

### 5.1 `fechas.py` — parser de fechas en español (lógica pura, sin IA)
Está muy bien pensado (devuelve None cuando duda y deja decidir a la IA). Mejoras:

- **[MEDIA] Sin tests = frágil.** Es el candidato #1 para tests (ver 4.3). Toda su
  lógica es determinista: entrada texto + `ahora` fijo → salida ISO. Perfecto para Ralph.
- **[BAJA] Cobertura de expresiones.** No entiende: "el lunes que viene", "este viernes",
  "a las 7 y media", "y cuarto", "dentro de un rato", "el 15 de junio" (día+mes sin año),
  "fin de semana". Anotado como mejora incremental, no urgente.
- **[BAJA] Ambigüedad documentada.** `_hora` descarta "a las 5" sin am/pm si `h<=7`
  (línea 49) para evitar errores; correcto, pero conviene un test que fije ese contrato
  para que nadie lo "arregle" por error.
- **[BAJA] `_fecha` con `fromisoformat`** puede lanzar `ValueError` con fechas tipo
  `2026-13-40`; hoy iría dentro de `parsear` y reventaría. Envolver en try/except y
  devolver None. (Caso raro pero real si el usuario teclea una fecha imposible.)

### 5.2 `busqueda.py` — búsqueda web sin dependencias
- **[MEDIA] El scraping de DuckDuckGo es frágil por diseño.** Depende de las clases
  CSS `result__a` / `result__snippet` con regex. Si DDG cambia el HTML, `_duckduckgo`
  devuelve `[]` en silencio (el `except` solo imprime). Wikipedia (API oficial) es estable.
  **Evidencia 2026:** "older scraping approaches using regex on specific CSS selectors may
  become outdated as DuckDuckGo updates its page structure... prefer the DDGS library."
  **Opciones:** (a) añadir la librería `ddgs` como respaldo opcional (rompe la regla de
  "sin dependencias"); (b) dejar Wikipedia como fuente primaria fiable y tratar DDG como
  "best effort"; (c) al menos un test/healthcheck que avise si DDG deja de devolver nada.
  **Recomendación:** (b)+(c). Mantener cero dependencias es valioso; solo hacer el fallo visible.
- **[BAJA] `_get` no fija `Accept-Language`** ni reintenta; un timeout deja la fuente vacía.
- **[BAJA] Posible `ReDoS` teórico** en la regex con `.*?` sobre HTML grande; en la
  práctica el HTML de DDG es acotado, riesgo bajo. Solo anotarlo.

### 5.3 `asistente.py` — CLI de resumen/recordatorios (cron/systemd)
- **[BAJA] Código duplicado:** `todos_los_chats` (asistente.py) y `chat_ids_permitidos`
  (bot.py) son casi idénticos. Unificar en un solo helper compartido cuando se haga el
  split de módulos (4.2).
- **[BAJA] `cargar_config` lee `token` y `chat_id`** pero el bot usa `chat_ids` (lista).
  Conviven dos esquemas por compatibilidad; documentado, no urgente.
- **[OK] Buen patrón:** intenta IA para el resumen y cae al `construir_resumen` clásico
  si falla. Mantener.

---

## Boceto LISTO PARA RALPH — `tests/test_fechas.py`

Propuesta de archivo de tests (con `pytest`). Fija `ahora` para que sea determinista.
Pensado como una "user story" autocontenida: crear `tests/`, añadir `pytest`, que pase.

```python
# tests/test_fechas.py
import datetime
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fechas

# Referencia fija: martes 2026-06-09, 14:00
AHORA = datetime.datetime(2026, 6, 9, 14, 0)

def test_manana_a_las_10():
    cuando, rep, txt = fechas.parsear("recuerdame llamar al banco mañana a las 10", AHORA)
    assert cuando == "2026-06-10T10:00"
    assert rep is None
    assert "banco" in txt.lower()

def test_en_2_horas():
    cuando, rep, txt = fechas.parsear("avisame en 2 horas sacar el pan", AHORA)
    assert cuando == "2026-06-09T16:00"
    assert "pan" in txt.lower()

def test_en_media_hora():
    cuando, _, _ = fechas.parsear("recuerdame en media hora estirar", AHORA)
    assert cuando == "2026-06-09T14:30"

def test_viernes_7pm():
    cuando, rep, txt = fechas.parsear("recuerdame el viernes a las 7pm cobrarle a Luis", AHORA)
    assert cuando == "2026-06-12T19:00"   # viernes siguiente al martes 9
    assert rep is None

def test_diario_pastilla():
    cuando, rep, txt = fechas.parsear("recuerdame todos los dias a las 7am tomar la pastilla", AHORA)
    assert rep == "diario"
    assert cuando.endswith("T07:00")

def test_hora_ambigua_devuelve_none():
    # "a las 5" sin am/pm es ambiguo -> None (lo decide la IA)
    assert fechas.parsear("recuerdame a las 5 algo", AHORA) is None

def test_sin_trigger_devuelve_none():
    assert fechas.parsear("hola que tal", AHORA) is None

def test_evento_dentista():
    fecha, hora, titulo = fechas.parsear_evento("agendame dentista mañana 10am", AHORA)
    assert fecha == "2026-06-10"
    assert hora == "10:00"
    assert "dentista" in titulo.lower()

def test_evento_sin_fecha_none():
    # un evento necesita fecha clara
    assert fechas.parsear_evento("agendame dentista", AHORA) is None
```
**Nota:** estos asserts hay que VERIFICARLOS ejecutando el parser real antes de fijarlos
(algunos contratos como "viernes siguiente" dependen de la lógica `% 7 or 7`). Ralph debe
ejecutar `pytest` y ajustar el esperado al comportamiento real, no al revés, salvo que
detecte un bug genuino (entonces, reportarlo aquí en MEJORAS.md, no cambiar fechas.py
sin tu visto bueno).

---

## ENFOQUE 6 — Despliegue / systemd (ciclo 3)

Los units están bien hechos. Reconocimiento de lo correcto:
- `agenda-bot.service`: `Restart=always`, `RestartSec=5`, `MemoryMax=300M`, `Nice=10`,
  `CPUWeight=20`, `NoNewPrivileges=true`, `After/Wants=network-online.target`. Sólido.
- Timers `Persistent=true`: si el equipo estaba apagado a las 6:00/22:00, se ejecuta al
  encender. Correcto para un portátil.

Mejoras posibles (todas BAJA prioridad, "endurecimiento"):
- **[BAJA] Hardening extra del bot.service:** añadir `ProtectSystem=strict`,
  `ProtectHome=read-only` con `ReadWritePaths=` para la carpeta del proyecto (necesita
  escribir `agenda.db` y `respaldos/`), `PrivateTmp=true`. Reduce daño si el proceso
  se ve comprometido. Cuidado: hay que listar bien los `ReadWritePaths` o el bot no
  podrá escribir la BD.
- **[BAJA] `agenda-noche/resumen.service` sin `[Install]`:** correcto, los disparan los
  timers; no hace falta. Solo confirmar que NO se intenten `enable` directamente.
- **[BAJA] Dependencia de red en los oneshot:** los resúmenes llaman a Telegram/IA pero
  no declaran `After=network-online.target`. Si el timer dispara justo al arrancar sin
  red, fallará el envío. Añadir `After=network-online.target` + `Wants=...` a los .service
  oneshot, o un pequeño reintento en `asistente.py`.
- **[BAJA] Ruta de Python fija** `/usr/bin/python3`: si usa venv en el futuro, romperá.
  Anotado por si migra a entorno virtual.
- **[BAJA] `instalar-timers.sh`** usa `set -e` y rutas con comillas: bien. No reinicia
  si `agenda-bot.service` no existe aún; no es crítico (es de instalación).

---

## Manejo de rate limits de Telegram (relacionado con 1.2)

### 6.x [PRIORIDAD MEDIA] `enviar_mensaje` no respeta `retry_after` ni `ok=false`
**Problema:** `asistente.enviar_mensaje` (asistente.py:69) hace `sendMessage` y solo
imprime si `ok` es false; no maneja el HTTP 429 con `retry_after`. Si el bot manda a
varios `chat_ids` (recordatorios, sugerencias) y Telegram limita, los mensajes se pierden.
**Evidencia 2026:** "When you exceed rate limits, the API responds 429 with a retry_after
field... your bot is blocked for that duration for ALL users. Always add ~10% jitter."
Límites prácticos (no oficiales): ~30 msg/s globales, **~1 msg/s por chat**.
**Boceto:**
```python
def enviar_mensaje(texto, token, chat_id, botones=None, _intentos=3):
    ...
    try:
        res = api_telegram("sendMessage", params, token)
    except urllib.error.HTTPError as e:
        if e.code == 429 and _intentos > 0:
            cuerpo = json.loads(e.read().decode("utf-8"))
            espera = cuerpo.get("parameters", {}).get("retry_after", 1)
            time.sleep(espera + 0.1 * espera)  # +10% jitter
            return enviar_mensaje(texto, token, chat_id, botones, _intentos - 1)
        raise
    ...
```
**Nota:** hoy el bot tiene 1-2 chats, así que el riesgo real es bajo; pero el envío en
bucle `for cid in chat_ids` (recordatorios vencidos) podría dispararlo. Prioridad MEDIA.

---

## Bocetos de tests adicionales (ciclo 3)

### `tests/test_procesar_simple.py` (LISTO PARA RALPH)
Prueba el parser por reglas de `bot.py`. Como `procesar_simple` recibe y muta `tareas`,
se le pasa un dict en memoria; NO toca la BD si se evita `fechas`/`db`. OJO: algunas
ramas llaman a `db` (completar_fase, recordatorios), así que conviene testear solo las
ramas puras o usar una BD temporal (ver test_db). Empezar por las puras:
```python
# tests/test_procesar_simple.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot

def test_agrega_pendiente():
    tareas = {"pendientes": [], "eventos": []}
    resp, cambio = bot.procesar_simple("agrega comprar pan", tareas, estricto=True)
    assert cambio is True
    assert "comprar pan" in tareas["pendientes"]

def test_evento_con_fecha_explicita():
    tareas = {"pendientes": [], "eventos": []}
    resp, cambio = bot.procesar_simple("evento 2026-06-15 10:00 reunion", tareas, estricto=True)
    assert cambio is True
    assert tareas["eventos"][0]["fecha"] == "2026-06-15"
    assert tareas["eventos"][0]["hora"] == "10:00"

def test_borra_pendiente_por_texto():
    tareas = {"pendientes": ["comprar pan", "pagar luz"], "eventos": []}
    resp, cambio = bot.procesar_simple("borra pagar luz", tareas, estricto=True)
    assert cambio is True
    assert "pagar luz" not in tareas["pendientes"]

def test_estricto_devuelve_none_si_no_entiende():
    tareas = {"pendientes": [], "eventos": []}
    assert bot.procesar_simple("cuentame un chiste", tareas, estricto=True) is None
```

### `tests/test_db.py` (LISTO PARA RALPH — usa BD temporal)
`db.py` usa una constante global `DB_PATH`. Para testear sin tocar `agenda.db` real,
hay que apuntar `db.DB_PATH` a un archivo temporal ANTES de `init_db()`:
```python
# tests/test_db.py
import sys, os, tempfile, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def cargar_db_temporal():
    import db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.DB_PATH = tmp.name
    db.JSON_VIEJO = tmp.name + ".nojson"  # que no importe tareas.json real
    db.init_db()
    return db, tmp.name

def test_pendientes_round_trip():
    db, ruta = cargar_db_temporal()
    try:
        db.guardar_tareas({"pendientes": ["a", "b"], "eventos": []})
        t = db.cargar_tareas()
        assert t["pendientes"] == ["a", "b"]
    finally:
        os.remove(ruta)

def test_recordatorio_vencido():
    db, ruta = cargar_db_temporal()
    try:
        db.add_recordatorio("2000-01-01T00:00", "antiguo")
        venc = db.recordatorios_vencidos()
        assert any(r["texto"] == "antiguo" for r in venc)
    finally:
        os.remove(ruta)

def test_proyecto_y_fase():
    db, ruta = cargar_db_temporal()
    try:
        db.add_proyecto("pizzeria")
        db.add_fase("pizzeria", "comprar horno")
        f = db.fase_actual("pizzeria")
        assert f["titulo"] == "comprar horno"
        comp, sig = db.completar_fase("pizzeria")
        assert comp["titulo"] == "comprar horno"
    finally:
        os.remove(ruta)
```
**Nota para Ralph:** reasignar `db.DB_PATH` funciona porque `conn()` lo lee en cada
llamada. Verificarlo ejecutando `pytest`. Limpiar siempre el archivo temporal y el
posible `-wal`/`-shm` que crea WAL.

---

## prd.json de ejemplo (estilo Ralph) — NO crear el archivo, es una plantilla

Si decides usar Ralph para estas mejoras, este sería un `prd.json` razonable. Las
stories están ordenadas por prioridad y son independientes/verificables. **Revísalo
antes de usarlo**; `passes:false` en todas para que Ralph las tome una a una.

```json
{
  "projectName": "Asistente Telegram - mejoras seguras",
  "branchName": "mejoras-asistente",
  "userStories": [
    {
      "id": "SEC-1",
      "title": "Crear .gitignore y config.json.example",
      "priority": 1,
      "passes": false,
      "description": "Crear asistente/.gitignore ignorando config.json, agenda.db, agenda.db-wal, agenda.db-shm, respaldos/, __pycache__/, *.pyc, tareas.json. Crear config.json.example con las mismas claves vacias (token, chat_ids, gemini_api_key, etc.). NO borrar ni modificar config.json real.",
      "acceptance": "git status no muestra config.json como rastreable; existe config.json.example sin secretos."
    },
    {
      "id": "BUG-1",
      "title": "Escapar HTML del texto dinámico en los mensajes",
      "priority": 2,
      "passes": false,
      "description": "Crear un helper esc() con html.escape(s, quote=False) y aplicarlo a TODO valor dinámico (pendientes, títulos de evento, notas, nombres de proyecto/fase, lecturas, respuesta libre de la IA) antes de interpolarlo en plantillas con etiquetas HTML. NO escapar las plantillas que ya contienen <b>/<i> del propio bot. Cubrir con test: un pendiente con '&' y con '<' debe quedar escapado.",
      "acceptance": "Enviar 'agrega pan & leche' o 'anota List<int>' no rompe el formato; test verifica el escapado."
    },
    {
      "id": "BOT-1",
      "title": "Persistir el offset de Telegram en la tabla estado",
      "priority": 3,
      "passes": false,
      "description": "En bot.py main(): inicializar offset desde db.estado_get('tg_offset') y guardarlo con db.estado_set tras procesar cada update. Reutilizar la tabla estado existente.",
      "acceptance": "Reiniciar el bot no reprocesa updates ya vistos; el valor persiste en agenda.db."
    },
    {
      "id": "TEST-1",
      "title": "Suite de tests para fechas.py",
      "priority": 4,
      "passes": false,
      "description": "Crear tests/test_fechas.py segun el boceto de MEJORAS.md, con ahora fijo. Ejecutar pytest y ajustar los esperados al comportamiento REAL del parser (si se halla un bug, anotarlo en MEJORAS.md, no cambiar fechas.py).",
      "acceptance": "pytest tests/test_fechas.py pasa en verde."
    },
    {
      "id": "BOT-2",
      "title": "Backoff exponencial en el bucle de getUpdates",
      "priority": 5,
      "passes": false,
      "description": "En bot.py, ante excepcion en el bucle principal usar espera que duplica (3->6->...->60s) y se resetea tras un ciclo exitoso. No tocar KeyboardInterrupt.",
      "acceptance": "Errores repetidos espacian los reintentos; un exito vuelve a 3s."
    },
    {
      "id": "TEST-2",
      "title": "Tests para db.py con BD temporal y procesar_simple",
      "priority": 6,
      "passes": false,
      "description": "Crear tests/test_db.py y tests/test_procesar_simple.py segun bocetos de MEJORAS.md. Usar BD temporal reasignando db.DB_PATH. Limpiar archivos temporales (incluido -wal/-shm).",
      "acceptance": "pytest pasa; no se modifica agenda.db real."
    },
    {
      "id": "IA-1",
      "title": "Validar formato de fecha/hora en ejecutar_acciones",
      "priority": 7,
      "passes": false,
      "description": "Anadir validacion semantica (AAAA-MM-DD, HH:MM, cuando ISO) antes de insertar acciones de la IA en la BD; ignorar o avisar si el formato es invalido. Cubrir con test.",
      "acceptance": "Una accion con fecha invalida no corrompe la BD y se reporta."
    },
    {
      "id": "SYS-1",
      "title": "Apagado limpio con handler de SIGTERM",
      "priority": 8,
      "passes": false,
      "description": "En bot.main(), registrar signal.signal(SIGTERM, ...) que haga parar.set(), DESPERTAR.set() y provoque el mismo cierre que KeyboardInterrupt, para que 'systemctl stop/restart' apague el hilo de recordatorios de forma ordenada.",
      "acceptance": "systemctl --user stop agenda-bot deja en journald el mensaje de apagado; el proceso termina sin matar el hilo a medias."
    },
    {
      "id": "MSG-1",
      "title": "Trocear mensajes de más de 4096 caracteres",
      "priority": 9,
      "passes": false,
      "description": "En asistente.enviar_mensaje, si el texto supera 4096 chars, partirlo por líneas (sin cortar palabras ni etiquetas) y enviarlo en varios mensajes; los botones solo en el último trozo. Cubrir con test de longitud.",
      "acceptance": "Un texto de >4096 chars se envía en varias partes; uno corto sigue en un solo mensaje."
    },
    {
      "id": "REC-1",
      "title": "No encolar avisos de planes escalonados con hora pasada",
      "priority": 10,
      "passes": false,
      "description": "En ejecutar_acciones (agregar_recordatorio), si 'cuando' ya pasó hace más de 5 min y la acción pertenece a un 'grupo' (plan escalonado), omitir ese aviso para evitar ráfaga inmediata. Un aviso suelto recién pasado (<5 min) sí puede dispararse. Cubrir con test.",
      "acceptance": "Una acción con grupo y cuando 2h en el pasado no se inserta; un aviso suelto 1 min en el pasado sí."
    },
    {
      "id": "BOT-3",
      "title": "Respetar retry_after (429) en enviar_mensaje",
      "priority": 11,
      "passes": false,
      "description": "En asistente.enviar_mensaje, capturar HTTPError 429, leer parameters.retry_after, esperar con ~10% jitter y reintentar (max 3). Mantener compatibilidad de la firma.",
      "acceptance": "Un 429 simulado provoca espera y reintento, no perdida del mensaje."
    }
  ]
}
```

---

## ENFOQUE 7 — Robustez de la cascada de IA (ciclo 4)

### 7.1 [PRIORIDAD MEDIA] `_llamar` no distingue timeout de error de cuota
**Problema:** en `gemini_ia._llamar`, un `socket.timeout`/`URLError` (red lenta) NO está
en el `except urllib.error.HTTPError`; cae como excepción genérica y rompe el bucle de
modelos, aunque el siguiente modelo podría responder. Solo los códigos 429/500/503
hacen `continue`; un timeout de red aborta toda la cascada Gemini.
**Evidencia 2026:** "Set explicit timeouts (10s razonable)... limit retries to 3-5...
exponential backoff 1s,2s,4s... add jitter."
**Boceto:**
```python
import socket
for modelo in MODELOS:
    try:
        ...
    except urllib.error.HTTPError as e:
        ultimo_error = e
        if e.code in (429, 500, 503):
            continue
        raise
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        ultimo_error = e   # red: probar el siguiente modelo igualmente
        continue
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        ultimo_error = e
        continue
```

### 7.2 [PRIORIDAD BAJA] Sin backoff entre intentos de modelos/respaldos
`_llamar` y `_llamar_respaldo` prueban modelos en ráfaga sin esperar. Si todos están
saturados (429), conviene un pequeño `time.sleep` con backoff entre intentos para no
martillar. Para un bot personal el volumen es bajo, así que es BAJA; anotado.

### 7.3 [PRIORIDAD BAJA] Timeout de 40s puede bloquear el bucle del bot
Relacionado con 1.4: cada `urlopen(..., timeout=40)` puede congelar `manejar_mensaje`
40s × (4 modelos + respaldos). Si se aborda concurrencia (1.4), bajar timeouts a ~15-20s
y dejar que la cascada lo compense reduce la espera percibida.

### 7.4 [OBSERVACIÓN] Desfase docs vs código en los modelos
`LEEME.md:60` menciona `gemini-flash-latest -> gemini-2.5-flash -> gemini-2.0-flash`,
pero `gemini_ia.MODELOS` lista `gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.0-flash,
gemini-2.0-flash-lite`. Menor: actualizar el LEEME para que coincida (o viceversa).

---

## ENFOQUE 8 — Observabilidad: `logging` en vez de `print` (ciclo 4)

### 8.1 [PRIORIDAD MEDIA-BAJA] Reemplazar `print("Aviso...")` por `logging`
**Problema:** hay ~15 `print("Aviso ...", e)` repartidos (bot.py, gemini_ia.py,
busqueda.py, asistente.py). Bajo systemd van a journald sin nivel, sin timestamp propio
ni contexto. Cuesta diagnosticar (¿fue red? ¿cuota? ¿qué chat?).
**Patrón mínimo propuesto (sin dependencias):**
```python
# en un nuevo log.py
import logging, os
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
def get(nombre):
    return logging.getLogger(nombre)
```
```python
# en cada módulo
import log
logger = log.get(__name__)
logger.warning("búsqueda IA falló: %s", e)   # en vez de print("Aviso busqueda IA:", e)
```
**Beneficio:** niveles (INFO/WARNING/ERROR), timestamps, `journalctl --user -u
agenda-bot -p warning` filtra solo problemas. **Contra:** es un cambio transversal
(muchos archivos) → mejor hacerlo DESPUÉS de tener tests (4.3) y, si se hace el split
(4.2), aprovechar para introducirlo. Apto para Ralph como story única "migrar print→logging".

---

## Plantilla propuesta — `config.json.example` (NO crear aún; parte de SEC-1)

Verificado en ciclo 4: `config.json` contiene estas claves (todas con valor real):
`token`, `chat_id`, `chat_ids` (lista), `gemini_api_key`, `openrouter_api_key`,
`xai_api_key`, `mistral_api_key`, `zhipu_api_key`. Falta `groq_api_key` (el código lo
soporta como primer respaldo, pero no está en config → ese respaldo no se usa hoy).

Contenido sugerido para `config.json.example` (a versionar; el real va al `.gitignore`):
```json
{
  "token": "PEGA_AQUI_EL_TOKEN_DE_BOTFATHER",
  "chat_ids": ["PEGA_TU_CHAT_ID", "PEGA_SEGUNDO_CHAT_ID_OPCIONAL"],
  "gemini_api_key": "PEGA_KEY_DE_AISTUDIO_GOOGLE_COM",
  "groq_api_key": "",
  "openrouter_api_key": "",
  "mistral_api_key": "",
  "zhipu_api_key": "",
  "xai_api_key": ""
}
```
**Nota:** `chat_id` (singular) queda como compatibilidad; en nuevas instalaciones basta
`chat_ids`. Documentar en LEEME que `groq_api_key` es el primer respaldo recomendado
(plan gratis y rápido).

---

## ENFOQUE 9 — Sanitización de HTML (ciclo 5) ⚠️ BUG REAL

### 9.1 [PRIORIDAD ALTA] Texto del usuario sin escapar en mensajes `parse_mode=HTML`
**Problema (bug reproducible):** `enviar_mensaje` (asistente.py:69) usa
`parse_mode: "HTML"`. En todo el código se interpola texto dinámico del usuario y de
la IA dentro de etiquetas, p. ej.:
- `f"📝 Agregado a pendientes: <i>{nuevo}</i>"` (bot.py)
- `f"🗒 Nota guardada:\n<i>{m.group(1).strip()}</i>"`
- títulos de eventos, nombres de proyectos, notas, lecturas...

Si el usuario escribe un pendiente con `<`, `>` o `&`, Telegram **rechaza el mensaje**
("can't parse entities") o lo renderiza mal. Casos cotidianos que lo disparan:
- `agrega comprar pan & leche`  → el `&` rompe el parseo
- `anota usar List<int> en el código`
- `agrega repasar 5 < x < 10`

**Evidencia 2026:** "With parse_mode HTML you must escape `<`, `>` and `&`... use
`html.escape()` on user-provided strings before including them in HTML messages."
(Caso idéntico reportado en python-telegram-bot #4257.)

**Solución propuesta (robusta y mínima):** escapar el texto dinámico con
`html.escape(..., quote=False)` JUSTO donde se interpola contenido del usuario. La forma
más segura y de menor esfuerzo es un helper y aplicarlo a los valores variables:
```python
import html
def esc(s):  # escapa solo lo dinámico; las etiquetas <b>/<i> fijas las pone el código
    return html.escape(str(s), quote=False)
# uso:
f"📝 Agregado a pendientes: <i>{esc(nuevo)}</i>"
```
**Cuidado:** NO escapar las cadenas que YA contienen las etiquetas `<b>`/`<i>` que pone
el propio bot (si se escapa todo el mensaje final, se ven las etiquetas como texto). Por
eso se escapa el valor, no la plantilla. La respuesta libre de la IA (`frase`) también
debería revisarse: la IA puede emitir `<` sin cerrar; conviene una pasada que cierre o
escape etiquetas no permitidas (Telegram solo admite un set: b,i,u,s,a,code,pre...).

**Para Ralph:** story de prioridad ALTA con test: enviar un pendiente con `&`/`<` y
verificar que el texto se escapa antes de construir el mensaje (testear la función que
formatea, no la red). Es de los pocos hallazgos que es un BUG, no solo una mejora.

---

## ENFOQUE 10 — Historial y ahorro de tokens (ciclo 5)

### 10.1 [OBSERVACIÓN, prioridad BAJA] El manejo de historial ya es razonable
`db.historial_add` guarda recortando a 1500 chars y poda a 30 filas por chat;
`historial_reciente` devuelve N (se piden 6 en bot.py) y `interpretar` recorta cada
mensaje a 400 chars y toma los últimos 10. Es un control de tokens sensato.

Mejoras menores posibles:
- **[BAJA] Doble recorte confuso:** se guarda a 1500 y luego se vuelve a recortar a 400
  al enviar. Unificar criterio (p. ej. guardar 1500 para mostrar al usuario, enviar 300-400
  a la IA está bien). Documentar el porqué.
- **[BAJA] Resumir en vez de truncar:** para conversaciones largas, en lugar de cortar a
  6 mensajes, se podría guardar un "resumen rodante". Complejidad alta para beneficio
  marginal en un bot personal → NO recomendado ahora.
- **[BAJA] `historial` crece sin TTL temporal:** se poda por cantidad (30) pero no por
  antigüedad; mensajes viejos persisten. Irrelevante en tamaño, anotado.

**Conclusión:** este flujo NO necesita cambios urgentes. Bien diseñado.

---

## ENFOQUE 11 — IA local con Ollama como respaldo offline (ciclo 5)

### 11.1 [PRIORIDAD BAJA — buena idea futura] Encaja casi sin código
**Idea (del LEEME.md):** usar Ollama (`ollama run qwen3:8b`) como respaldo cuando no hay
internet o se agotaron todas las cuotas. **Viabilidad: ALTA y barata**, porque Ollama
expone un endpoint **compatible con la API de OpenAI** en
`http://localhost:11434/v1/chat/completions`, exactamente el formato que ya usa
`_llamar_respaldo` y la lista `RESPALDOS`.

**Boceto de integración (1 línea en la lista + sin clave):**
```python
RESPALDOS = [
    ...los actuales...,
    # Respaldo LOCAL (offline). No requiere API key; se ignora si Ollama no corre.
    ("ollama_local", "http://localhost:11434/v1/chat/completions", "qwen3:8b"),
]
```
Ajustes necesarios en `_llamar_respaldo`:
- Para `ollama_local` no exigir clave (`key` puede ir vacía; Ollama no valida Authorization).
- Bajar el `timeout` o detectar rápido si el puerto 11434 no responde (para no esperar
  40s cuando Ollama no está instalado). Un intento de conexión corto (~2s) basta.
- `response_format: json_object` lo soporta Ollama en modelos recientes; si falla, usar
  el prompt para forzar JSON.

**Ventajas:** funciona sin internet y sin cuota; privacidad total. **Contra:** requiere
16 GB RAM y tener Ollama corriendo; en un portátil modesto puede ir lento. Por eso es
**último de la cascada** y opcional. Excelente "red de seguridad" para cortes de internet.

**Para Ralph:** story de prioridad baja: añadir el respaldo Ollama con detección de
puerto y timeout corto, sin romper la cascada si no está disponible. Testear que, con
Ollama apagado, la cascada lo salta sin colgarse.

---

## ENFOQUE 12 — Fechas/horas y zona horaria (ciclo 6)

### 12.1 [PRIORIDAD BAJA para este equipo] Todo usa `datetime.now()` naive (sin tz)
**Hecho:** la zona del equipo es **America/Caracas (-04, sin horario de verano/DST)**.
Todo el código (`db.py`, `fechas.py`, `gemini_ia.py`, `asistente.py`) usa
`datetime.datetime.now()` *naive* y compara como string `"%Y-%m-%dT%H:%M"`.
**Veredicto:** para Venezuela funciona bien hoy: no hay DST que provoque saltos, y la
comparación de strings ISO es correcta porque el formato es ordenable lexicográficamente.
**Riesgos (todos de baja probabilidad para este usuario):**
- Si el portátil viaja a otra zona o se cambia la zona del sistema, los recordatorios
  guardados conservan la hora *de pared* anterior (pueden adelantarse/atrasarse de sentido).
- Si en el futuro Venezuela reintrodujera DST, los avisos cerca del salto podrían
  duplicarse o saltarse (problema clásico de naive datetimes).
- El bot y los timers asumen la misma zona; si se ejecuta en un servidor en otra zona,
  los "buenos días 6:00" llegarían a hora del servidor.
**Recomendación (opcional, BAJA):** documentar explícitamente la suposición "hora local
del equipo" en el código, o fijar la zona con `zoneinfo`:
```python
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/Caracas")
ahora = datetime.datetime.now(TZ)
```
Pero como se compara con strings sin offset, migrar a aware datetimes obliga a tocar
TODO el formateo `%Y-%m-%dT%H:%M`. **No vale la pena ahora**; solo anotar la suposición.
**NO es un quick win.** Es deuda latente, no un problema actual.

### 12.2 [PRIORIDAD MEDIA] Recordatorios con hora en el PASADO se disparan de golpe
**Problema (analizado en código):** `db.recordatorios_vencidos` devuelve todo lo que
cumpla `enviado=0 AND cuando<=ahora`. `ejecutar_acciones` inserta los `agregar_recordatorio`
de la IA SIN validar que `cuando` sea futuro. Escenario real:
- En un **plan escalonado** (acción con `grupo`), la IA genera varios avisos (arranque,
  avance, víspera, entrega). Si calcula mal y pone el primero "hace 1 hora" (o el usuario
  pide algo para "hoy a las 9" cuando ya son las 10), ese aviso es `cuando <= ahora` y el
  hilo `vigilar_recordatorios` lo envía **de inmediato**. Con varios en el pasado → **ráfaga
  de mensajes** al instante (spam), poco amable.
- Peor con `insistir_min`: un recordatorio insistente en el pasado se reenvía ya mismo y
  se reprograma "+30 min", funcionando, pero el primer disparo es inesperado.

**Propuesta (MEDIA, con test):** al ejecutar `agregar_recordatorio`, validar/clampear:
```python
ahora_iso = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
if cuando and cuando < ahora_iso:
    # opciones: (a) descartar el aviso pasado del plan escalonado;
    #           (b) avisar al usuario que esa hora ya pasó;
    #           (c) si es repetir/insistir, dejar que el siguiente cálculo lo ajuste.
    # Recomendado para planes (grupo): omitir los avisos cuyo 'cuando' ya pasó.
    continue  # o no insertarlo
```
Sutil: a veces SÍ se quiere disparar "ahora" (un recordatorio para "en 1 min" que tardó
en procesarse). Por eso conviene una **tolerancia** (p. ej. ignorar solo si pasó hace
>5 min) y tratar distinto los planes escalonados (omitir avisos vencidos) de un aviso suelto.
**Para Ralph:** story MEDIA con test: una acción con `cuando` 2h en el pasado y `grupo`
no debe encolar ese aviso; un aviso suelto 1 min en el pasado sí puede dispararse.

---

## Boceto — `tests/README.md` (ciclo 6, LISTO PARA RALPH)

```markdown
# Tests del asistente

Tests con `pytest` (sin dependencias externas más allá de pytest).

## Cómo correr
    pip install pytest        # solo la primera vez
    cd asistente
    pytest -v                 # todos
    pytest tests/test_fechas.py -v   # uno solo

Los tests usan una BD temporal (reasignan `db.DB_PATH`), así que **no tocan
`agenda.db` real** ni envían nada a Telegram (no hay red en los tests).

## Qué cubre cada archivo
| Archivo | Cubre |
|---|---|
| `test_fechas.py` | Parser de fechas/horas en español (`fechas.parsear`, `parsear_evento`): mañana, "en N horas", días de la semana, repeticiones, casos ambiguos que deben devolver None. |
| `test_procesar_simple.py` | Parser por reglas de `bot.py` (agrega/borra/evento) sobre un dict en memoria, sin IA ni red. |
| `test_db.py` | CRUD de `db.py` con BD temporal: pendientes round-trip, recordatorios vencidos, proyectos y fases. |

## Convenciones
- Fijar siempre un `ahora` explícito en los tests de fechas (determinismo).
- Limpiar archivos temporales en `finally` (incluidos `-wal`/`-shm` de WAL).
- Si un test revela un bug en el código de producción, **anotarlo en MEJORAS.md**
  y NO cambiar el código sin revisión humana (salvo que la story lo autorice).
```

---

## ENFOQUE 13 — Límite de 4096 caracteres de Telegram (ciclo 7)

### 13.1 [PRIORIDAD MEDIA-BAJA] Mensajes largos pueden superar el tope y fallar
**Hecho:** `sendMessage` de Telegram **rechaza** textos de más de 4096 caracteres
(error "message is too long"). Varias funciones construyen mensajes que CRECEN con los
datos del usuario y no se trocean:
- `texto_lista`: junta proyectos + pendientes + eventos + recordatorios. Con muchos
  recordatorios/pendientes puede pasarse.
- `texto_proyectos(completo=True)` y `texto_fases`: listan TODAS las fases de TODOS los
  proyectos. Un proyecto con muchas fases lo dispara.
- El resumen redactado por la IA está acotado (~14 líneas) → bajo riesgo.
**Por qué es MEDIA-BAJA:** hoy el volumen del usuario es pequeño, así que es improbable;
pero cuando ocurra, el fallo es total (no se envía NADA y solo se imprime el error).
**Propuesta (robusta, en un solo sitio):** trocear en `enviar_mensaje` por si acaso:
```python
LIMITE = 4096
def enviar_mensaje(texto, token, chat_id, botones=None):
    if len(texto) <= LIMITE:
        return _enviar_una(texto, token, chat_id, botones)
    # trocear por líneas sin cortar palabras; los botones van solo en el último trozo
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > LIMITE:
            partes.append(actual); actual = ""
        actual += (linea + "\n")
    if actual: partes.append(actual)
    res = None
    for i, p in enumerate(partes):
        res = _enviar_una(p, token, chat_id, botones if i == len(partes)-1 else None)
    return res
```
**Cuidado:** no cortar en medio de una etiqueta `<b>...</b>` (trocear por `\n` lo evita
en la práctica porque las etiquetas no cruzan líneas en este código). Apto para Ralph
como story MEDIA-BAJA con test de longitud (>4096 → varias partes).

---

## ENFOQUE 14 — Casos borde de `parsear_evento` (ciclo 7)

Revisado el flujo `EVENTO` + `_hora` + `_fecha` en `fechas.py`. Observaciones:

- **[OK] Evento sin hora:** "agendame dentista mañana" → `(fecha, None, "dentista")`.
  Correcto: la hora es opcional, la fecha obligatoria (si no hay fecha → None y decide la IA).
- **[OK] Limpieza de título:** tras `_quitar` de fecha/hora, hace
  `re.sub(r"\s+"," ",resto).strip(" ,.;:-")`, que colapsa espacios dobles dejados por
  `_quitar` y recorta signos. Bien pensado.
- **[BAJA] Palabras conectivas sobrantes:** frases como "agendame cita con Ana el lunes"
  → quita "el lunes" → título "cita con Ana" ✔. Pero "agendame para el lunes reunión"
  podría dejar "para reunión" (la preposición suelta queda). Menor y poco frecuente.
- **[BAJA] "a las" huérfano:** si `_hora` no captura (p.ej. "a las cinco" en letras),
  el "a las" se queda en el título. El parser no entiende horas escritas en palabras;
  ya devuelve None en ambigüedad, así que normalmente cae a la IA. Anotado, no urgente.
- **[BAJA] Doble fecha:** "agendame X mañana 2026-07-01" → `_hora` no aplica, `_fecha`
  toma la primera coincidencia ISO (regex ISO va primero) y deja "mañana" en el título.
  Caso muy raro (usuario da dos fechas); aceptable.
**Conclusión:** `parsear_evento` es sólido; los flecos son de baja frecuencia y el diseño
"ante la duda, None y que decida la IA" los cubre. Lo mejor aquí es **fijar el
comportamiento con tests** (ya en el boceto test_fechas.py) más que cambiar la lógica.

---

## ENFOQUE 15 — Apagado limpio bajo systemd (ciclo 8)

### 15.1 [PRIORIDAD MEDIA] El bot solo captura `KeyboardInterrupt`, no `SIGTERM`
**Problema (analizado):** `agenda-bot.service` es `Type=simple` con `Restart=always`.
Cuando haces `systemctl --user restart/stop`, systemd envía **SIGTERM**. Pero
`bot.main()` solo maneja `KeyboardInterrupt` (que viene de SIGINT/Ctrl+C). Con SIGTERM,
Python ejecuta su handler por defecto y **termina el proceso sin lanzar la excepción**,
así que el bloque de limpieza (`parar.set()`, join del hilo) **nunca corre**. El hilo
`vigilar_recordatorios` (daemon) muere de golpe.
**Impacto real:** bajo, porque (a) WAL + `db.py` abre/cierra una conexión por llamada,
así que no quedan transacciones colgando; (b) el hilo es daemon. Pero si justo se estaba
escribiendo un respaldo o un recordatorio, podría cortarse a medias. Y los reinicios son
frecuentes (cada cambio de código). Por eso MEDIA, no baja.
**Propuesta (pequeña y robusta):** instalar un handler de SIGTERM que provoque el mismo
cierre limpio que Ctrl+C:
```python
import signal
def main():
    ...
    parar = threading.Event()
    def _apagar(signum, frame):
        print("Señal recibida, apagando...")
        parar.set()
        DESPERTAR.set()      # despierta el hilo de recordatorios para que salga ya
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _apagar)
    ...
```
Alternativa más limpia: no relanzar `KeyboardInterrupt` sino comprobar `parar.is_set()`
en el bucle principal y romper. Cualquiera de las dos sirve. Apto para Ralph (story MEDIA
con verificación: `systemctl stop` deja log "apagando" en journald).
**Bonus:** considerar `TimeoutStopSec` en el .service por si el cierre tardara; con el
handler, el cierre es inmediato.

---

## ENFOQUE 16 — Configurabilidad de la sugerencia proactiva (ciclo 8)

### 16.1 [PRIORIDAD BAJA] Ventana 9-21h y 4h están hardcodeadas
**Hecho:** `HORAS_PROACTIVO=(9,21)` y `CADA_PROACTIVO=4*3600` son constantes de módulo
en `bot.py`. Funcionan bien, pero quien quiera cambiar el horario o la frecuencia tiene
que editar el código. Para un asistente personal, mover esto a `config.json` es natural.
**Propuesta (BAJA, agradable):**
```python
# en config.json (opcional, con defaults):
#   "proactivo_horas": [9, 21], "proactivo_cada_horas": 4, "proactivo_activo": true
pro = cfg.get("proactivo_horas", [9, 21])
HORAS_PROACTIVO = (pro[0], pro[1])
CADA_PROACTIVO = cfg.get("proactivo_cada_horas", 4) * 3600
if not cfg.get("proactivo_activo", True):
    # saltar sugerencia_proactiva por completo
```
**Beneficio:** quien trabaje de noche o no quiera sugerencias puede ajustarlo sin tocar
código. **Contra:** añade superficie de config. Como las constantes ya son sensatas,
es un "nice to have", no urgente. Documentar en `config.json.example` si se hace.
**Observación de diseño (OK):** la lógica ya respeta `ultima_actividad` y `ultima_sugerencia`
para no molestar — buen detalle de UX. No tocar esa parte.

---

## 📊 Matriz de propuestas (resumen, ciclo 9)

Ordenadas por prioridad sugerida. Esfuerzo y riesgo en escala baja/media/alta.
"Bug" = corrige un fallo real; "Mejora" = incremental; "Seguridad" = riesgo de fuga.

| Story | Enfoque | Tipo | Prioridad | Esfuerzo | Riesgo de tocar | Para Ralph |
|---|---|---|---|---|---|---|
| SEC-1 | 4.1 | Seguridad | 1 | Bajo | Nulo | Sí ✅ |
| BUG-1 | 9.1 | Bug | 2 | Bajo | Bajo | Sí ✅ |
| BOT-1 | 1.1 | Mejora | 3 | Muy bajo | Bajo | Sí ✅ |
| TEST-1 | 4.3 | Mejora (tests) | 4 | Medio | Nulo | Sí ✅ |
| BOT-2 | 1.2 | Mejora | 5 | Bajo | Bajo | Sí ✅ |
| TEST-2 | 4.3 | Mejora (tests) | 6 | Medio | Nulo | Sí ✅ |
| IA-1 | 2.2 | Mejora | 7* | Medio | Bajo | Sí ✅ |
| SYS-1 | 15.1 | Mejora | 7 | Bajo | Bajo | Sí ✅ |
| MSG-1 | 13.1 | Bug latente | 8 | Bajo | Bajo | Sí ✅ |
| REC-1 | 12.2 | Bug latente | 9 | Medio | Medio | Sí (con cuidado) |
| BOT-3 | 6.x | Mejora | 10 | Bajo | Bajo | Sí ✅ |

(* IA-1 e SYS-1 quedaron ambos cerca de 7 en distintos ciclos; trátalos como "media".)

**No incluidas como story (requieren diseño humano o son opinión):**
| Tema | Enfoque | Por qué no es story directa |
|---|---|---|
| Concurrencia / workers | 1.4 | Riesgo de carreras; serializar por chat_id primero |
| Split de bot.py (~880 líneas) | 4.2 | Refactor amplio; hacer DESPUÉS de tener tests |
| responseSchema nativo Gemini | 2.1 | Toca doble camino Gemini/respaldos OpenAI |
| Migrar a python-telegram-bot | 1.3 | Reescritura; el enfoque sin deps es una virtud |
| logging en vez de print | 8.1 | Transversal; aprovechar al hacer el split |
| Ollama offline | 11.1 | Opcional; depende de tener Ollama + 16GB RAM |
| Zona horaria aware (zoneinfo) | 12.1 | Deuda latente; sin DST en Venezuela hoy |
| FTS5 para notas | 3.3 | Innecesario con pocas notas |

---

## Cómo encaja con Ralph

Las propuestas más adecuadas para Ralph (pequeñas, verificables, independientes):
- 9.1 escapar HTML del texto dinámico (ALTA — es un BUG real, con test sencillo) ← **arreglar pronto**
- 4.1 crear `.gitignore` + `config.json.example` (trivial, ALTA prioridad de seguridad) ← **lo más urgente y fácil**
- 1.1 persistir offset usando la tabla `estado` (1 story, trivial de verificar)
- 1.2 backoff exponencial (1 story)
- 4.3 / test_fechas.py — suite de tests (boceto ya listo arriba) ← **mayor retorno**
- 2.2 validación semántica de acciones (1 story con tests)
- 5.1 try/except en `_fecha` para fechas imposibles (1 story con test)
- 5.2 hacer visible el fallo de DuckDuckGo (log/healthcheck, 1 story)

Las que NO conviene dar a Ralph sin diseño humano previo:
- 1.4 concurrencia / workers (riesgo de carreras)
- 4.2 split de bot.py (refactor amplio; hacer DESPUÉS de tener tests)
- 2.1 responseSchema (toca el doble camino Gemini/respaldos)

---

## Pendientes para próximos ciclos de esta noche
- [x] Verificar `.gitignore` y si `config.json` está ignorado (4.1) → CONFIRMADO: no hay .gitignore
- [x] Leer `fechas.py`, `asistente.py`, `busqueda.py` (enfoque 5)
- [x] Esbozar `tests/test_fechas.py` (hecho arriba)
- [x] Revisar systemd (`.service`/`.timer`) e `instalar-timers.sh` (enfoque 6)
- [x] Esbozar `tests/test_procesar_simple.py` y `tests/test_db.py` (hechos)
- [x] Investigar rate limits de Telegram sendMessage (6.x retry_after)
- [x] Redactar `prd.json` de ejemplo dentro de MEJORAS.md
- [x] urllib timeouts/reintentos en la cascada de IA (enfoque 7)
- [x] logging vs print (enfoque 8)
- [x] documentar config.json.example (verificadas las claves reales)
- [x] sección "Quick wins" añadida al inicio
- [x] Sanitización HTML del texto dinámico (enfoque 9 — BUG real hallado)
- [x] Flujo de historial / ahorro de tokens (enfoque 10 — ya razonable)
- [x] Viabilidad de Ollama offline (enfoque 11 — encaja casi sin código)
- [x] Zona horaria / fechas naive (enfoque 12.1 — deuda latente, baja para este equipo)
- [x] Recordatorios con hora en pasado (enfoque 12.2 — story REC-1 añadida)
- [x] README de tests/ esbozado (ciclo 6)
- [x] Límite 4096 chars / troceo de mensajes (enfoque 13 — story MSG-1)
- [x] Casos borde de parsear_evento (enfoque 14 — sólido, mejor fijarlo con tests)
- [x] Consistencia de prd.json (IDs y prioridades 1-9 sin choques)
- [x] sugerencia_proactiva configurable (enfoque 16 — story baja)
- [x] Apagado limpio SIGTERM (enfoque 15 — story SYS-1, MEDIA)
- [x] Cabos sueltos: respaldos/ (datos personales → ignorar), tareas.json (vacío), repo SIN archivos rastreados (refuerza SEC-1)
- [x] Matriz de propuestas añadida (ciclo 9)
- [x] Consistencia: enfoques 1-16, prd con prioridades 1-10
- [x] prd.json: prioridades renumeradas a 1-11 únicas (resuelto choque TEST-1/BOT-2 en ciclo 10)
- [x] CIERRE (06:26): sección "## Cierre de la noche" escrita. Fin de la investigación nocturna.

---

## Cierre de la noche

> Investigación terminada a las **2026-06-11 06:26**. Toda la noche en ciclos de
> ~28 min, sin tocar el código: **solo este archivo**. Lo que sigue es el mapa
> para que decidas qué aplicar (a mano o con Ralph).

### 1) Índice de enfoques (1-16)

| # | Enfoque | Núcleo de la propuesta |
|---|---------|------------------------|
| 1 | Bucle del bot (Telegram) | 1.1 persistir `offset` en BD (ALTA), 1.2 backoff exponencial, 1.3 migrar a python-telegram-bot, 1.4 concurrencia |
| 2 | Integración IA (Gemini) | 2.1 `responseSchema` en vez de pedir JSON en el prompt, 2.2 validar la salida, 2.3 contador de uso |
| 3 | Base de datos SQLite | PRAGMAs ya buenos (WAL/busy_timeout); opción FTS5 para búsqueda de historial |
| 4 | Arquitectura/seguridad | 4.1 secretos + `.gitignore` (ALTA), 4.2 trocear `bot.py`, 4.3 suite de tests (ALTA) |
| 5 | Revisión por archivo | `fechas.py` (puro, ideal para tests), `busqueda.py` (DDG frágil), `asistente.py` (sin chunking/429) |
| 6 | systemd + rate limits | servicios/timers bien; añadir `After=network-online.target`; respetar `retry_after` (MEDIA) |
| 7 | Cascada de IA | capturar `socket.timeout` además de HTTPError; timeouts por proveedor (MEDIA) |
| 8 | Observabilidad | `logging` con niveles en vez de `print` |
| 9 | **Sanitización HTML** | 9.1 escapar texto dinámico con `html.escape` — **BUG real** (ALTA) |
| 10 | Historial / tokens | ya razonable (tope 1500 chars, poda a 30) |
| 11 | Ollama offline | encaja como un respaldo más, casi sin código |
| 12 | Fechas | 12.1 datetimes naive (deuda latente, baja); 12.2 avisos con hora pasada (MEDIA) |
| 13 | Límite 4096 chars | trocear mensajes largos sin cortar etiquetas (MEDIA-BAJA) |
| 14 | `parsear_evento` | sólido; conviene fijarlo con tests |
| 15 | Apagado limpio | handler de `SIGTERM` para `systemctl stop/restart` (MEDIA) |
| 16 | Proactividad configurable | horas/frecuencia de `sugerencia_proactiva` desde config (BAJA) |

### 2) User stories del prd.json (11, por prioridad)

| Prioridad | ID | Título |
|-----------|----|--------|
| 1 | SEC-1 | Secretos fuera del repo: `.gitignore` + rotar claves |
| 2 | BUG-1 | Escapar HTML del texto dinámico en mensajes |
| 3 | BOT-1 | Persistir `offset` de getUpdates en agenda.db |
| 4 | TEST-1 | Suite de tests para `fechas.py` |
| 5 | BOT-2 | Backoff exponencial en el bucle de getUpdates |
| 6 | TEST-2 | Tests para `db.py` (BD temporal) y `procesar_simple` |
| 7 | IA-1 | Validar formato de fecha/hora en `ejecutar_acciones` |
| 8 | SYS-1 | Apagado limpio con handler de SIGTERM |
| 9 | MSG-1 | Trocear mensajes de más de 4096 caracteres |
| 10 | REC-1 | No encolar avisos de planes escalonados con hora pasada |
| 11 | BOT-3 | Respetar `retry_after` (429) en `enviar_mensaje` |

### 3) Los 4 a tocar primero (quick-wins / bugs)

1. **9.1 / BUG-1 — Escapar HTML (BUG real).** Si tu texto trae `& < >`, `sendMessage`
   con `parse_mode=HTML` falla y el mensaje no llega. Pasar el texto dinámico por
   `html.escape(...)` antes de incrustarlo. Arreglo pequeño, impacto alto.
2. **4.1 / SEC-1 — `.gitignore` + ROTAR claves.** El repo no tiene `.gitignore` y
   `config.json` (token + 5 API keys) no está ignorado; un `git add .` los publicaría.
   Crear `.gitignore` (config.json, agenda.db*, respaldos/, tareas.json) **y rotar el
   token de @BotFather y todas las API keys**, porque ya estuvieron en texto plano
   (lo dice el propio LEEME.md).
3. **1.1 / BOT-1 — Persistir el offset.** Hoy `offset` vive en memoria; al reiniciar
   el bot puede reprocesar updates viejos. Guardarlo en `agenda.db` (tabla k/v de
   `estado_get/set`) y leerlo al arrancar.
4. **12.2 / REC-1 — Avisos de planes escalonados en pasado.** Un plan con horas ya
   pasadas dispara una ráfaga de avisos de golpe. Omitir los avisos de grupo cuyo
   `cuando` quedó >5 min en el pasado.

### 4) Buenos días ☀️

Buenos días. Pasé la noche revisando foros y buenas prácticas y dejé todo
ordenado aquí, **sin tocar una sola línea del código**. Tienes 16 enfoques y un
`prd.json` listo para Ralph con 11 historias priorizadas. Si solo quieres empezar
por algo concreto: arregla el **escape de HTML** (bug real y rápido) y crea el
**`.gitignore` rotando las claves** (lo más urgente por seguridad). Lo demás puede
ir cayendo a su ritmo. Que descanses y que tengas un gran día. 💪
