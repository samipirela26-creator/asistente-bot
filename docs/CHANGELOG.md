# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/) y
versionado [SemVer](https://semver.org/lang/es/). El bot es stdlib pura en
runtime; `ruff`/`mypy` son solo de desarrollo/CI.

## [3.6.0] — 2026-06-22

### Añadido
- **Devolver un avance de proyecto**: al completar una fase, Larry ofrece un
  botón **↩️ Devolver avance** que la regresa a pendiente y borra ese registro
  de actividad del día para no inflar la racha (`db.descompletar_fase`,
  callback `proy_undo`). Pensado para deshacer un toque accidental.
- **Botón de partes en el menú** (**🌅 Buenos días/noches**) para activar o
  desactivar el parte automático cuando se quiera, y nueva acción
  `asistente.py preguntar-partes` que ofrece el parte a todos los usuarios
  registrados que aún no han decidido.

## [3.5.0] — 2026-06-22

### Añadido
- **Parte automático opt-in para cada usuario**: Larry pregunta una sola vez,
  por botones, si el usuario desea el parte de buenos días y el resumen
  nocturno (`db.set_partes`/`get_partes`/`usuarios_con_partes`). Los partes
  matutino y nocturno (`asistente.py resumen`/`noche`) y la lámina dominical
  (`tarjeta`) se envían además a los externos que dijeron que sí, **cada uno
  con SUS propios datos** y sin IA (para no multiplicar cuota ni carga en la
  Lenovo). Las cuentas del dueño siguen recibiéndolo por configuración.
- **Registro de todos los usuarios**: nueva tabla `usuarios` que anota a
  cualquiera que escriba al bot —aunque no cree ni una tarea— con su primer y
  último contacto (`db.registrar_visto`, `usuarios_registrados`). El comando
  `usuarios` ahora los lista a TODOS (antes solo veía a quien tenía datos) y
  muestra su preferencia de partes.

### Corregido
- El comando `usuarios` ya no se dejaba fuera a quienes solo conversaban con el
  bot sin generar datos.

## [3.4.0] — 2026-06-15

### Añadido
- **Notas de voz (solo entender)**: Larry transcribe los audios y los procesa
  como si fueran texto. Motor de transcripción **Gemini multimodal**
  (`gemini_ia.transcribir`: manda el audio inline en base64, pide la
  transcripción literal en español, tolerante a fallos —devuelve `None` y no
  lanza). Elegido por ser lo más ligero para la Lenovo: cero carga local, sin
  modelos que descargar ni dependencias nativas. Documentado vosk (Apache-2.0,
  offline) como alternativa OSS si en el futuro se quiere sin nube.
- **Descarga de archivos de Telegram**: `asistente.descargar_archivo(file_id)`
  resuelve la ruta con `getFile` y baja los bytes con tope de tamaño
  (2 MB por defecto) para no ahogar la máquina. Devuelve `None` si algo falla.
- **Rama de voz en el bucle `getUpdates`**: nuevas claves `voice`/`audio` del
  mensaje se enrutan a `bot.manejar_voz`, que descarga, transcribe, hace un eco
  discreto de lo entendido (`🎙️ …`) y lo pasa por `manejar_mensaje`. Métrica
  `audios`.

## [3.3.0] — 2026-06-14

### Añadido
- **Tarjeta de progreso (PNG)**: nuevo módulo `tarjeta.py` que dibuja —con la
  máquina, no la IA— una lámina «estilo D» (anillo con el % global de avance +
  barras por proyecto), alimentada por SQLite (`progreso_proyecto`, `racha`,
  `actividad`). Stdlib pura: `zlib`+`struct` y una fuente bitmap 5×7 propia; sin
  Pillow ni matplotlib. Se pide a demanda con el botón **📊 Mi progreso**.
- **Lámina semanal automática**: nueva acción `asistente.py tarjeta` y timer
  `agenda-tarjeta.timer` (domingos 9:00) que manda un saludo con versículo y, a
  continuación, la imagen del progreso de la semana. Añadida al instalador
  portátil `instalar-timers.sh`.
- **Registro del avance del día**: el parte nocturno pregunta por botones si se
  avanzó en algo no anotado; al responder, el bot lo guarda con
  `db.log_actividad("manual", ...)` y lo suma a la racha/actividad.
- **Envío de imágenes**: `asistente.enviar_foto()` arma `multipart/form-data` a
  mano sobre `urllib` para `sendPhoto` (devuelve siempre un `dict`, nunca lanza).
- **Versículo del día**: carpeta editable `versiculos/` (100 versículos sobre
  constancia, en voz de Larry) y módulo `versiculos.py` que la máquina relee y
  escoge uno al azar. Se anexa a los partes matutino y nocturno.

## [3.2.0] — 2026-06-14

### Añadido
- **Trato personalizado**: Larry pregunta al usuario su nombre y, por botones,
  su tratamiento (**señor / señora / señorita**). Luego alterna entre
  «<título> <Nombre>» y el «<título>» a secas en cada respuesta
  (`db.get_nombre`/`set_nombre`/`get_titulo`/`set_titulo`/`tratamiento`,
  inyectado en el prompt de la IA). Onboarding una sola vez, controlado por
  `ONBOARDING_DESDE`.
- **Botón ✏️ Mi nombre** en el menú para cambiar el nombre cuando se quiera.

### Cambiado
- **Permiso de administrador**: los comandos técnicos (`estado`, `metricas`,
  `usuarios`) los reconoce ahora `es_admin()` para TODAS las cuentas personales
  del config, no solo la primera. `creador()` sigue siendo la cuenta única
  destino de las alertas técnicas.

## [3.0.0] — 2026-06-12

Hito de calidad de ingeniería: el bot pasa de «funciona» a «mantenible y
observable». Se sube a un *major* porque cambia el comportamiento de las
notificaciones (insistencia ahora es opt-in) y se consolida el modelo interno.

### Añadido
- **Insistencia a pedido**: al crear un recordatorio con intervalo, el bot
  pregunta por botones cuántas veces insistir, con opción **🔥 Súper
  insistente** (repite hasta marcar *Hecho*).
- **Silencio nocturno** para re-insistencias: las repeticiones automáticas que
  caerían de madrugada se difieren a la mañana. La primera entrega y los
  resúmenes matutino/nocturno no se tocan.
- **Métricas de observabilidad**: contadores atómicos (`mensajes`, `botones`,
  `ia_n`, `ia_fallos`, latencia media de IA) y comando `/metricas` para el
  administrador.
- **Esquema de BD versionado** con `PRAGMA user_version` y migraciones
  ordenadas e idempotentes (`db.SCHEMA_VERSION`).
- **Validación de configuración** al arranque (`validar_config`): avisa de
  token/chat_id/silencio mal formados sin romper.
- **Tests end-to-end de los handlers** con la API de Telegram mockeada y tests
  del manejo de red tipado. Suite de 70 tests.
- **CI con lint**: `ruff` bloqueante y `mypy` tolerante, además de la suite en
  Python 3.11 y 3.13.
- **Documentación técnica** (`ARQUITECTURA.md`): hilos, flujo de un mensaje,
  modelo multiusuario, persistencia y despliegue.

### Cambiado
- `asistente.api_telegram` ya **no lanza**: distingue 429 / 5xx / 4xx /
  timeout / respuesta inválida y siempre devuelve un `dict`.
- Los anuncios de actualización y las alertas de máquina van **solo al
  administrador** (`creador`).

### Corregido
- El recordatorio que se repetía pasada la medianoche: la insistencia
  automática ya no molesta de madrugada.
- Eliminados imports muertos detectados por el linter.

## [2.3.0] — anterior
- Migración del hogar al Lenovo 24/7, auto-actualización y arreglo de la fuga
  de conexiones SQLite.
