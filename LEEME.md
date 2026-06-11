# Asistente personal por Telegram

Asistente que corre solo en tu PC (Python + SQLite, software libre, gratis).
Te ayuda a organizarte: tareas, eventos, recordatorios que insisten,
proyectos por fases y sugerencias segun tu momento.

---

## Que hace

- **Tareas y eventos**: agregar, borrar y listar escribiendole con naturalidad.
- **Recordatorios con hora**: avisa a la hora exacta aunque no le escribas;
  pueden repetirse (diario/semanal) o insistir cada 30 min hasta que digas
  que ya lo hiciste.
- **Proyectos por fases**: cargas un proyecto con sus fases en orden y el bot
  te dice "la que sigue es esta, despues esta".
- **Sugerencias por contexto**: le describes tu situacion ("estoy esperando,
  tengo internet y 15 min") y te propone que fase avanzar, incluyendo lectura
  o tiempo devocional.
- **Resumen diario** cada mañana.
- **IA con Gemini** para entender frases libres; si se queda sin cuota, cae a
  comandos basicos.
- **Dos cuentas de Telegram** comparten la misma agenda.

## Archivos clave

| Archivo | Que es |
|---|---|
| `bot.py` | El bot que escucha y responde (cerebro principal) |
| `gemini_ia.py` | Conexion con la IA de Gemini |
| `db.py` | Base de datos SQLite (`agenda.db`) |
| `asistente.py` | Resumen diario y utilidades (sacar chat_id, prueba) |
| `config.json` | Token, las dos cuentas y la API key |
| `agenda-bot.service` | Arranque automatico al encender la PC |

## Como se maneja (servicio)

```
systemctl --user start agenda-bot.service     # encender
systemctl --user restart agenda-bot.service   # reiniciar tras cambios
systemctl --user status agenda-bot.service    # ver estado
```

Para probar a mano: para el servicio, ejecuta `python bot.py` (Ctrl+C para
salir) y al terminar vuelve a dejar el servicio encendido.

## Como hablarle

Primero intenta comandos simples (instantaneos, gratis); si no entiende,
le pasa el mensaje a Gemini. Ejemplos:

- "recuerdame llamar al banco mañana a las 10:30"
- "avisame todos los dias a las 7am tomar la pastilla"
- "agendame dentista mañana 10am"
- "ya pague el banco, quitalo"
- "que tengo pendiente?" / "que recordatorios tengo?"
- `lista`, `resumen`, `ayuda` (comandos basicos sin IA)

Si Gemini esta saturado o sin cuota, el bot prueba varios modelos
(gemini-flash-latest -> gemini-2.5-flash -> gemini-2.0-flash) y si nada
funciona usa los comandos basicos. La cuota gratuita se repone sola cada dia.

## Configuracion inicial (resumen)

1. Crear bot con **@BotFather** (`/newbot`) y copiar el token a `config.json`.
2. Sacar chat_id: escribirle al bot y ejecutar `python asistente.py chatid`;
   pegar el numero en `config.json`.
3. Probar: `python asistente.py prueba`.
4. La API key de Gemini va en `gemini_api_key` de `config.json`
   (gratis en aistudio.google.com).

## Nuevo (2026-06-10)

- **Botones en los recordatorios**: llegan con `✅ Hecho` y `⏰ +30 min`.
- **Sugerencia proactiva**: si llevas 4+ horas sin escribir (entre 9am y 9pm),
  el bot te propone solo una fase corta que puedas avanzar.
- **Racha y progreso**: al completar una fase te dice "3 de 8" y tus dias
  seguidos avanzando.
- **Minutos por fase**: di "esta fase toma 15 min" y las sugerencias
  filtraran por tu tiempo disponible.
- **Plan del dia**: el resumen matutino ahora lo redacta la IA con un orden
  sugerido (si falla, usa el resumen clasico).
- **Lecturas**: "quede en Juan 5" y luego "por donde iba?".
- **Resumen nocturno**: `python asistente.py noche` (programable con cron).
- **Notas rapidas**: "anota idea X", luego "notas" o "que anote de X?".
- **Memoria**: recuerda los ultimos 10 mensajes; entiende "mejor ponlo a las 5".
- **Respaldo automatico diario** de agenda.db en `respaldos/` (guarda 7).
- **Plan escalonado de avisos**: si dices que algo tiene fecha limite
  ("entrego el informe el viernes a las 10"), crea varios recordatorios
  que suben de tono segun la urgencia. Al marcar `✅ Hecho` en cualquiera
  (o decir "ya lo hice") se apagan todos los del grupo.

## Optimizaciones (2026-06-10)

- Comandos directos (agrega/borra/evento/lista/recordatorios/"ya lo hice")
  se resuelven al instante SIN gastar cuota de Gemini.
- Si la IA falla, el bot avisa con los comandos que sí entiende (ya no
  responde solo "no entendí"); la IA además pregunta en vez de rendirse.
- Base de datos en modo WAL con índices: más rápida y sin bloqueos.
- El vigilante de recordatorios duerme hasta el próximo aviso (menos CPU).
- El resumen diario llega a las dos cuentas.
- El servicio limita memoria y corre con prioridad baja.

## Pendientes

1. Confirmar que la segunda cuenta ya responde (prueba en curso).
2. Tras probar a mano, volver a dejar el servicio:
   `systemctl --user start agenda-bot.service`.
3. **Seguridad**: regenerar el token (`/revoke` en BotFather) y la API key
   (aistudio.google.com), porque se compartieron en texto.
4. Automatizar el resumen de cada mañana (timer de systemd o cron).

## Ideas para mas adelante

- Conectar con Google Calendar.
- Que el resumen lo redacte la IA con tono mas natural.
- IA local con Ollama (offline): `ollama run qwen3:8b` con 16 GB de RAM.
