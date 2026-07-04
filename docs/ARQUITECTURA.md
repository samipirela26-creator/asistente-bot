# Arquitectura del asistente

Documento técnico para quien mantenga el bot. El objetivo del proyecto es un
asistente personal por Telegram que corre en una sola máquina modesta, **sin
dependencias de pip** (solo la biblioteca estándar de Python). `ruff` y `mypy`
se usan únicamente en CI/desarrollo, nunca en tiempo de ejecución.

## Visión general

```
            Telegram  ───getUpdates(long-poll)──►  bot.py (hilo principal)
               ▲                                        │
               │ sendMessage                            ├─ rutea cada update
               └────────────────────────────────────────┤   (mensaje o botón)
                                                         ▼
                                                   db.py  (SQLite + WAL)
                                                         ▲
   hilo de recordatorios ──vigila vencidos──────────────┘
   (cada ~minuto)         envía avisos a su dueño

   timers de systemd (procesos aparte, NO el bot):
     06:00  asistente.py resumen   → resumen matutino
     22:00  asistente.py noche     → resumen nocturno
     cada 5 min  chequear_salud.py → alerta si el latido se enfría
```

## Estructura del repo

```
asistente/
├── src/       módulos Python (bot.py, db.py, asistente.py, ...) — ver tabla abajo
├── docs/      este archivo y el resto de la documentación
├── scripts/   instaladores (systemd) y mantenimiento (backup offsite)
├── systemd/   .timer que los instaladores copian tal cual
├── tests/     suite de unittest
└── config.json, tareas.json, agenda.db, respaldos/, versiculos/   datos en la raíz
```
Los módulos en `src/` se importan entre sí por nombre (`import db`, `import fechas`,
etc.) sin paquete ni imports relativos: alcanza con que vivan juntos en `src/`,
porque Python agrega el directorio del script que se ejecuta a `sys.path`. Los
que ubican datos hermanos (`db.py`, `asistente.py`, `bot.py`, `versiculos.py`)
calculan `BASE_DIR` subiendo un nivel desde `src/` para seguir encontrando
`agenda.db`, `config.json`, `tareas.json`, `.bot.lock` y `versiculos/` en la
raíz del repo.

## Módulos

| Archivo | Responsabilidad |
|---|---|
| `bot.py` | Proceso de larga vida. Long-poll de Telegram, ruteo de mensajes/botones, hilo de recordatorios, métricas. |
| `asistente.py` | Utilidades de Telegram (`api_telegram`, `enviar_mensaje`), carga/validación de config, y los comandos one-shot `resumen`/`noche`/`recordatorios`. |
| `db.py` | Toda la persistencia: SQLite con WAL, esquema versionado, datos por dueño, métricas, respaldo diario. |
| `gemini_ia.py` | Interpretación con IA (Gemini y respaldos). Devuelve acciones estructuradas. |
| `busqueda.py` | Búsqueda web sin API key (Wikipedia + DuckDuckGo). |
| `fechas.py` | Parseo de fechas/horas en lenguaje natural en español. |
| `sistema.py` | Salud de la máquina (CPU, RAM, disco) para alertas. |
| `chequear_salud.py` | Proceso externo: avisa por Telegram si el bot dejó de latir. |

## Hilos (dentro de `bot.py`)

El bot usa **dos hilos**, sin librerías de async:

1. **Hilo principal (`main`)** — hace long-polling a `getUpdates` (timeout 50s).
   Por cada update: aplica rate-limit (`permitido`), fija el dueño con
   `db.como_dueno(...)` y llama a `manejar_mensaje` o `manejar_boton`. Persiste
   el `offset` en la BD para no reprocesar updates viejos tras un reinicio, y
   escribe un **latido** (`estado['latido']`) en cada ciclo.

2. **Hilo de recordatorios (`vigilar_recordatorios`, daemon)** — despierta cada
   minuto (o antes, vía el `threading.Event` `DESPERTAR`, cuando un mensaje pudo
   crear un recordatorio). Envía los vencidos, gestiona la insistencia y las
   alertas de máquina. Trabaja siempre como `DUENO_PRINCIPAL` pero envía cada
   aviso al dueño de la fila.

**Coordinación:** `DESPERTAR` (un `Event`) lo activa el hilo principal tras
procesar un mensaje para que el de recordatorios reaccione al instante. El
apagado limpio se hace con `SIGTERM` (lo manda `systemctl stop/restart`): se
setea `parar` y se despierta el hilo daemon para que termine.

La concurrencia sobre SQLite es segura porque cada operación abre y cierra su
conexión dentro de un `with db.conn()` (context manager), con `WAL` y
`busy_timeout=10s`. Los contadores de métricas se incrementan con una sola
sentencia SQL atómica (no read-modify-write en Python).

## Flujo de un mensaje de texto

`manejar_mensaje` aplica una **cascada de lo barato a lo caro** para no gastar
cuota de IA si no hace falta:

1. **Atajos** (`menu`, `proyectos`, `intereses`, `metricas`) → respuesta directa.
2. **Búsqueda web** (`busca X` / `investiga X`) → Wikipedia + DuckDuckGo, y la IA
   redacta sólo si hay API key.
3. **Atajos instantáneos** (`atajo`: lista, resumen, ayuda) → gratis, sin IA.
4. **Parser por reglas estricto** (`procesar_simple(..., estricto=True)`) →
   comandos claros (recordar/agendar) resueltos al instante y gratis.
5. **IA** (`gemini_ia.interpretar`) → frases libres; devuelve *acciones* que
   `ejecutar_acciones` aplica (crear recordatorio, proyecto, etc.). Se mide la
   latencia y, si todo falla, se cuenta en `metrica('ia_fallos')` y se responde
   con un mensaje de ayuda y comandos directos.
6. **Respaldo sin IA** (`procesar_simple` no estricto) cuando no hay API key.

Los botones inline (`callback_data` como `rec_done:5`, `ins_set:5:30:3`,
`menu:lista`) los procesa `manejar_boton`, que confirma a Telegram con
`answerCallbackQuery` y nunca lanza ante datos corruptos.

## Modelo multi-usuario (aislamiento por «dueño»)

Cada fila de las tablas de datos lleva una columna `dueno`. El mapeo
`chat_id → dueno` lo resuelve `dueno_de(emisor, cfg)`. Hay dos formas de fijar
el dueño activo:

- `db.set_dueno(d)` — default FIJO del hilo (lo usa el hilo de recordatorios,
  que siempre es el principal).
- `with db.como_dueno(d):` — **acotado y a prueba de excepciones**; es el patrón
  correcto para los handlers por-mensaje, porque restaura el dueño anterior al
  salir (incluso si algo falla). El hilo principal envuelve cada
  `manejar_mensaje`/`manejar_boton` en este context manager.

Reparto de envíos:
- `destinos_de(dueno, cfg)` — a qué chat(s) va un aviso de ese dueño.
- `creador(cfg)` — el chat del administrador; los anuncios de actualización y
  las alertas técnicas de la máquina van **sólo** ahí.

## Persistencia y migraciones

SQLite con WAL. El esquema está **versionado** con `PRAGMA user_version` y la
constante `db.SCHEMA_VERSION`. En el arranque, `init_db` crea las tablas base
(`CREATE TABLE IF NOT EXISTS`) y, si la BD está por detrás, aplica en orden las
migraciones de `_aplicar_migraciones` (cada paso idempotente) y sube la versión.
Para añadir un cambio de esquema: agregar un bloque `if desde < N:` y subir
`SCHEMA_VERSION` a `N` — sin reordenar ni borrar los bloques previos.

`respaldo_diario()` copia `agenda.db` a `respaldos/` una vez al día y conserva
los últimos 7.

## Robustez de red

`asistente.api_telegram` **nunca lanza**: distingue 429 (respeta `retry_after`),
5xx (reintenta con backoff), 4xx (permanente, no reintenta), timeout/red caída
(reintenta) y respuesta no-JSON. Siempre devuelve un `dict {"ok": ...}`, de modo
que un bache de red no tumba al que llama.

## Observabilidad

- **Latido**: el hilo principal escribe `estado['latido']` cada ciclo;
  `chequear_salud.py` (timer de systemd) alerta al creador si se enfría.
- **Métricas**: contadores atómicos en la tabla `estado` (`metrica:*`):
  `mensajes`, `botones`, `ia_n`, `ia_fallos`, latencia media de IA. El comando
  `/metricas` (solo el administrador) los muestra.
- **Logs**: `logging` estándar; en systemd los recoge journald.

## Configuración

`config.json` (y variables de entorno `AGENDA_*` que lo pisan, para rotar
tokens sin editar el archivo). `validar_config` revisa la forma al arrancar y
**avisa por log** de problemas (token mal formado, chat_id no numérico, franja
de silencio fuera de 0-23) sin romper. El archivo se fuerza a permisos `600`.
Secretos (token y API keys) y `agenda.db` están en `.gitignore` y nunca se
commitean.

## Despliegue

`instalar-timers.sh` genera las unidades de systemd de usuario con la ruta real
de la máquina: el servicio `agenda-bot` (el proceso de larga vida) y los timers
de resumen matutino/nocturno y de chequeo de salud. **Solo una máquina** debe
correr el bot a la vez (Telegram devuelve 409 Conflict si dos hacen polling).

## Tests

`python3 -m unittest discover -s tests`. Cubren: utilidades (silencio, rate
limit, config, insistencia, esquema, métricas), los handlers end-to-end con la
API de Telegram mockeada (sin red), y el manejo de red tipado de `api_telegram`.
CI (`.github/workflows/tests.yml`) corre los tests en Python 3.11 y 3.13, `ruff`
(bloqueante) y `mypy` (tolerante, aún no bloquea).
