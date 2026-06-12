# Runbook operativo — Asistente (bot de agenda)

Guía corta para cuando algo va mal. El bot es **stdlib pura** en runtime;
corre como servicios **systemd de usuario** en una sola máquina (la principal
es el **Lenovo**; el Dell queda apagado).

> Regla de oro: **solo UNA máquina** puede correr el bot a la vez. Dos bots con
> el mismo token compiten y Telegram responde `409 Conflict`.

## Servicios

| Unidad | Qué hace | Cuándo |
|---|---|---|
| `agenda-bot.service` | El bot (escucha mensajes + hilo de recordatorios) | Siempre activo |
| `agenda-resumen.timer` | Resumen matutino | 06:00 |
| `agenda-noche.timer` | Resumen nocturno | 22:00 |
| `agenda-salud.timer` | Healthcheck externo | Periódico |
| `agenda-actualizar.timer` | Auto-actualización (git pull + restart) | Periódico |

Comandos base:
```bash
systemctl --user status  agenda-bot.service
systemctl --user restart agenda-bot.service
systemctl --user stop    agenda-bot.service
journalctl --user -u agenda-bot.service -f        # logs en vivo
journalctl --user -u agenda-bot.service --since today
```

---

## Síntoma → causa → arreglo

### 1. Llegan avisos duplicados / `409 Conflict` en los logs
**Causa:** hay DOS instancias con el mismo token (p.ej. Dell **y** Lenovo).
El bot ahora avisa al admin tras varios 409 seguidos.
**Arreglo:** decide la máquina buena (Lenovo) y apaga la otra:
```bash
# En la máquina que NO debe correr:
systemctl --user stop agenda-bot.service
systemctl --user disable agenda-bot.service
```
El lock local (`.bot.lock`) ya evita dos instancias en la **misma** máquina;
el 409 solo aparece si son **máquinas distintas**.

### 2. Un aviso sonó de madrugada
- Las **re-insistencias** automáticas y las entregas cuya hora **no** la fijó
  el usuario a propósito se difieren a las **07:00** (silencio 23:00–07:00).
- Si el usuario **pidió** esa hora exacta (`hora_explicita=1`), suena a su hora.
**Si insiste de más:** toca **✅ Hecho** en el mensaje para apagarlo, o:
```bash
python3 -c "import db; \
  [db.marcar_enviado(dict(r)) for r in db.recordatorios_vencidos()]"   # con cuidado
```

### 3. El bot no responde / no hay red
El bot avisa al admin si lleva >15 min sin hablar con Telegram o si la IA
falla en cadena. Para diagnosticar:
```bash
journalctl --user -u agenda-bot.service --since "30 min ago"
systemctl --user restart agenda-bot.service
```

### 4. La IA no contesta ("Ninguna IA respondió")
Suele ser cuota o red. El bot sigue aceptando **comandos directos**
(recordatorios/eventos por reglas). Revisa claves en `config.json`
(`gemini_api_key`, etc.) y la conectividad. Se recupera solo.

### 5. Base de datos corrupta o se perdió `agenda.db`
Hay respaldo diario en `respaldos/agenda-AAAA-MM-DD.db` (se guardan 7).
**Restaurar** (probado por `tests/test_respaldo.py`):
```bash
systemctl --user stop agenda-bot.service
cp respaldos/agenda-AAAA-MM-DD.db agenda.db      # la copia más reciente sana
rm -f agenda.db-wal agenda.db-shm                # restos de WAL, si los hay
systemctl --user start agenda-bot.service
```

---

## Secretos y datos (NUNCA al repo)
`config.json` (token + API keys) y `agenda.db` están en `.gitignore` y nunca
deben subirse. El repo `asistente-bot` es **privado**; el Lenovo usa una
**deploy key de solo lectura**. Rotación de token: variables `AGENDA_*` +
`chmod 600 config.json`.

## Verificación rápida tras tocar algo
```bash
python3 -m unittest discover -s tests
python3 -c "import bot"
```
