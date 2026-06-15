# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/) y
versionado [SemVer](https://semver.org/lang/es/). El bot es stdlib pura en
runtime; `ruff`/`mypy` son solo de desarrollo/CI.

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
