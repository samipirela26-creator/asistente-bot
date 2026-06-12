# -*- coding: utf-8 -*-
"""
Capa de base de datos (SQLite) para el asistente.

Sustituye al antiguo tareas.json pero mantiene el mismo "formato" de
diccionario para pendientes y eventos, asi el resto del codigo casi no cambia.
Ademas anade una tabla de RECORDATORIOS con fecha/hora y repeticion.

SQLite viene incluido en Python: no hay que instalar nada.
"""

import os
import json
import sqlite3
import datetime
import threading
import contextlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agenda.db")
JSON_VIEJO = os.path.join(BASE_DIR, "tareas.json")

# --- Multiusuario: cada cuenta tiene sus propios datos aislados. ---
# El "dueno" identifica de quien son los datos. Las cuentas personales del
# config comparten el dueno DUENO_PRINCIPAL (asi heredan lo que ya existia);
# cualquier otro usuario nuevo usa su propio chat_id como dueno.
DUENO_PRINCIPAL = "principal"
_local = threading.local()


def set_dueno(dueno):
    """Fija un dueno por defecto FIJO para el hilo actual. Úsalo solo para hilos
    que trabajan siempre como el mismo dueno (p.ej. el de recordatorios). Para
    código que cambia de dueno entre mensajes usa 'with como_dueno(dueno):',
    que lo hace explícito y lo restaura al salir (más seguro)."""
    _local.dueno = dueno or DUENO_PRINCIPAL


def _d(dueno=None):
    """Dueno efectivo: el explicito, si no el del hilo, si no el principal."""
    if dueno:
        return dueno
    return getattr(_local, "dueno", DUENO_PRINCIPAL)


@contextlib.contextmanager
def como_dueno(dueno):
    """Fija el dueno SOLO dentro de este bloque y lo restaura al salir (incluso
    si hay excepcion). Hace el dueno EXPLICITO en el sitio donde se procesa cada
    mensaje, en vez de depender de un set_dueno suelto que quedaba pegado al hilo
    indefinidamente. Uso:

        with db.como_dueno(dueno):
            manejar_mensaje(...)
    """
    previo = getattr(_local, "dueno", None)
    _local.dueno = dueno or DUENO_PRINCIPAL
    try:
        yield
    finally:
        if previo is None:
            try:
                del _local.dueno
            except AttributeError:
                pass
        else:
            _local.dueno = previo


@contextlib.contextmanager
def conn():
    """Abre una conexion, hace commit al salir bien (rollback si hay error) y
    SIEMPRE la cierra. Antes se usaba 'with sqlite3.connect(...)', que hace
    commit pero NO cierra: cada operacion dejaba una conexion abierta y los
    descriptores se agotaban ('unable to open database file')."""
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")     # lecturas y escrituras no se bloquean
    c.execute("PRAGMA busy_timeout=10000")   # espera en vez de fallar si esta ocupada
    c.execute("PRAGMA synchronous=NORMAL")   # rapido y seguro con WAL
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS pendientes (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                texto  TEXT NOT NULL,
                dueno  TEXT
            );
            CREATE TABLE IF NOT EXISTS eventos (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha  TEXT NOT NULL,
                hora   TEXT,
                titulo TEXT NOT NULL,
                dueno  TEXT
            );
            CREATE TABLE IF NOT EXISTS recordatorios (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                cuando  TEXT NOT NULL,   -- ISO: 2026-06-09T15:30
                texto   TEXT NOT NULL,
                repetir TEXT,            -- NULL | 'diario' | 'semanal'
                enviado INTEGER DEFAULT 0,
                dueno   TEXT
            );
            CREATE TABLE IF NOT EXISTS proyectos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT NOT NULL,
                descripcion TEXT,
                dueno       TEXT
            );
            CREATE TABLE IF NOT EXISTS fases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                proyecto_id INTEGER NOT NULL,
                orden       INTEGER NOT NULL,
                titulo      TEXT NOT NULL,
                contexto    TEXT,          -- etiquetas libres: "internet,15min,telefono,investigar"
                hecho       INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS notas (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                fecha TEXT NOT NULL,      -- ISO
                dueno TEXT
            );
            CREATE TABLE IF NOT EXISTS lecturas (
                nombre      TEXT NOT NULL,     -- ej: 'biblia', 'libro pizzeria'
                dueno       TEXT NOT NULL,
                marcador    TEXT NOT NULL,     -- ej: 'Juan 5'
                actualizado TEXT NOT NULL,
                PRIMARY KEY (nombre, dueno)
            );
            CREATE TABLE IF NOT EXISTS actividad (
                fecha TEXT NOT NULL,      -- AAAA-MM-DD
                tipo  TEXT NOT NULL,      -- 'fase' | 'pendiente'
                texto TEXT,
                dueno TEXT
            );
            CREATE TABLE IF NOT EXISTS historial (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                rol     TEXT NOT NULL,    -- 'user' | 'model'
                texto   TEXT NOT NULL,
                fecha   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS estado (
                clave TEXT PRIMARY KEY,
                valor TEXT
            );
            """
        )
        # Migracion suave: anadir columna insistir_min a recordatorios si falta.
        cols = [r[1] for r in c.execute("PRAGMA table_info(recordatorios)")]
        if "insistir_min" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN insistir_min INTEGER")
        # Migracion: grupo para recordatorios escalonados (varios avisos de una tarea).
        if "grupo" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN grupo TEXT")
        # Migracion: cuantas veces MAS hay que volver a insistir (lo elige el
        # usuario por botones; 0 = avisa una sola vez).
        if "insistir_veces" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN insistir_veces INTEGER DEFAULT 0")
        # Migracion: minutos estimados por fase.
        cols_f = [r[1] for r in c.execute("PRAGMA table_info(fases)")]
        if "minutos" not in cols_f:
            c.execute("ALTER TABLE fases ADD COLUMN minutos INTEGER")
        # Migracion multiusuario: anadir columna 'dueno' a las tablas por-usuario
        # y asignar los datos viejos al dueno principal (eran globales).
        for tabla in ("pendientes", "eventos", "recordatorios", "proyectos",
                      "notas", "actividad"):
            cols_t = [r[1] for r in c.execute(f"PRAGMA table_info({tabla})")]
            if "dueno" not in cols_t:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN dueno TEXT")
            c.execute(f"UPDATE {tabla} SET dueno=? WHERE dueno IS NULL",
                      (DUENO_PRINCIPAL,))
        # lecturas tenia 'nombre' como PK unica; ahora la clave es (nombre,dueno)
        # para que dos personas puedan tener la misma lectura. Se reconstruye.
        cols_l = [r[1] for r in c.execute("PRAGMA table_info(lecturas)")]
        if "dueno" not in cols_l:
            c.executescript(
                """
                CREATE TABLE lecturas_new (
                    nombre      TEXT NOT NULL,
                    dueno       TEXT NOT NULL,
                    marcador    TEXT NOT NULL,
                    actualizado TEXT NOT NULL,
                    PRIMARY KEY (nombre, dueno)
                );
                INSERT INTO lecturas_new (nombre, dueno, marcador, actualizado)
                    SELECT nombre, 'principal', marcador, actualizado FROM lecturas;
                DROP TABLE lecturas;
                ALTER TABLE lecturas_new RENAME TO lecturas;
                """
            )
        # Indices para consultas frecuentes.
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_pend "
                  "ON recordatorios (enviado, cuando)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fases_pend "
                  "ON fases (proyecto_id, hecho, orden)")
    # Migracion: los intereses eran globales (clave 'intereses'); ahora son
    # por dueno ('intereses:principal'). Se copian una sola vez.
    if estado_get("intereses") is not None and estado_get(_clave_intereses()) is None:
        estado_set(_clave_intereses(), estado_get("intereses"))
    _migrar_json()


def _migrar_json():
    """Si existe el viejo tareas.json y la BD esta vacia, lo importa una vez."""
    if not os.path.exists(JSON_VIEJO):
        return
    with conn() as c:
        ya = c.execute("SELECT COUNT(*) FROM pendientes").fetchone()[0]
        ya += c.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
        if ya:
            return
    try:
        with open(JSON_VIEJO, encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return
    guardar_tareas({
        "pendientes": datos.get("pendientes", []),
        "eventos": datos.get("eventos", []),
    })


# ------------------------------------------------- pendientes y eventos (dict)
def cargar_tareas(dueno=None):
    """Devuelve {'pendientes':[...], 'eventos':[...]} del dueno dado/actual."""
    d = _d(dueno)
    with conn() as c:
        pend = [r["texto"] for r in c.execute(
            "SELECT texto FROM pendientes WHERE dueno=? ORDER BY id", (d,))]
        ev = []
        for r in c.execute("SELECT fecha, hora, titulo FROM eventos "
                           "WHERE dueno=? ORDER BY fecha, hora", (d,)):
            e = {"fecha": r["fecha"], "titulo": r["titulo"]}
            if r["hora"]:
                e["hora"] = r["hora"]
            ev.append(e)
    return {"pendientes": pend, "eventos": ev}


def guardar_tareas(tareas, dueno=None):
    """Reemplaza pendientes y eventos del dueno dado/actual."""
    d = _d(dueno)
    with conn() as c:
        c.execute("DELETE FROM pendientes WHERE dueno=?", (d,))
        c.execute("DELETE FROM eventos WHERE dueno=?", (d,))
        for p in tareas.get("pendientes", []):
            c.execute("INSERT INTO pendientes (texto, dueno) VALUES (?, ?)", (p, d))
        for e in tareas.get("eventos", []):
            c.execute(
                "INSERT INTO eventos (fecha, hora, titulo, dueno) VALUES (?, ?, ?, ?)",
                (e.get("fecha", ""), e.get("hora"), e.get("titulo", ""), d),
            )


# --------------------------------------------------------------- recordatorios
def add_recordatorio(cuando_iso, texto, repetir=None, insistir_min=None,
                     grupo=None, dueno=None, insistir_veces=0):
    """Crea un recordatorio y devuelve su id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO recordatorios (cuando, texto, repetir, insistir_min, "
            "grupo, dueno, insistir_veces) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cuando_iso, texto, repetir, insistir_min, grupo, _d(dueno),
             insistir_veces),
        )
        return cur.lastrowid


def configurar_insistencia(rec_id, minutos, veces, dueno=None):
    """Define cada cuántos minutos y cuántas veces MÁS insistir un recordatorio
    (lo que el usuario elija por botones). Devuelve True si lo encontró."""
    with conn() as c:
        cur = c.execute(
            "UPDATE recordatorios SET insistir_min=?, insistir_veces=? "
            "WHERE id=? AND dueno=?",
            (minutos, veces, rec_id, _d(dueno)),
        )
        return cur.rowcount > 0


def _borrar_grupo(c, grupo):
    """Borra todos los avisos del mismo grupo (plan escalonado)."""
    n = c.execute("SELECT COUNT(*) FROM recordatorios WHERE grupo=?",
                  (grupo,)).fetchone()[0]
    c.execute("DELETE FROM recordatorios WHERE grupo=?", (grupo,))
    return n


def listar_recordatorios(dueno=None):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM recordatorios WHERE enviado=0 AND dueno=? ORDER BY cuando",
            (_d(dueno),))]


def borrar_recordatorio(objetivo, dueno=None):
    """objetivo: numero (posicion en la lista) o texto a buscar."""
    pendientes = listar_recordatorios(dueno)
    objetivo = str(objetivo).strip()
    elegido = None
    if objetivo.isdigit():
        idx = int(objetivo) - 1
        if 0 <= idx < len(pendientes):
            elegido = pendientes[idx]
    else:
        for r in pendientes:
            if objetivo.lower() in r["texto"].lower():
                elegido = r
                break
    if elegido:
        with conn() as c:
            if elegido.get("grupo"):
                elegido["borrados"] = _borrar_grupo(c, elegido["grupo"])
            else:
                c.execute("DELETE FROM recordatorios WHERE id=?", (elegido["id"],))
        return elegido
    return None


def recordatorios_vencidos(ahora=None, dueno=None):
    """Recordatorios vencidos de UN dueno (por defecto, todos los dueños).
    El hilo de recordatorios usa dueno=None para barrer a todo el mundo y
    enviar cada aviso a su destinatario (cada fila trae su 'dueno')."""
    if ahora is None:
        ahora = datetime.datetime.now()
    ahora_iso = ahora.strftime("%Y-%m-%dT%H:%M")
    q = "SELECT * FROM recordatorios WHERE enviado=0 AND cuando<=?"
    args = [ahora_iso]
    if dueno is not None:
        q += " AND dueno=?"
        args.append(dueno)
    q += " ORDER BY cuando"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def proximo_recordatorio(dueno=None):
    """ISO del recordatorio pendiente mas cercano (de un dueno o de todos)."""
    q = "SELECT cuando FROM recordatorios WHERE enviado=0"
    args = []
    if dueno is not None:
        q += " AND dueno=?"
        args.append(dueno)
    q += " ORDER BY cuando LIMIT 1"
    with conn() as c:
        r = c.execute(q, args).fetchone()
    return r["cuando"] if r else None


def marcar_enviado(recordatorio):
    """Procesa un recordatorio que ya se envio.
    - insistente: se re-agenda dentro de X minutos (sigue avisando hasta que
      lo borres con 'ya lo hice').
    - repetido (diario/semanal): crea la siguiente ocurrencia.
    - normal: se marca como enviado y no vuelve.
    """
    ahora = datetime.datetime.now()
    insistir = recordatorio.get("insistir_min")
    veces = recordatorio.get("insistir_veces") or 0
    with conn() as c:
        # Insistencia ACOTADA: solo vuelve a avisar las veces que el usuario
        # pidió (se eligen por botones). Cada reenvío descuenta una.
        if insistir and veces > 0:
            siguiente = (ahora + datetime.timedelta(minutes=int(insistir))).strftime("%Y-%m-%dT%H:%M")
            c.execute("UPDATE recordatorios SET cuando=?, insistir_veces=? WHERE id=?",
                      (siguiente, veces - 1, recordatorio["id"]))
            return
        c.execute("UPDATE recordatorios SET enviado=1 WHERE id=?", (recordatorio["id"],))
        rep = recordatorio.get("repetir")
        if rep in ("diario", "semanal"):
            base = datetime.datetime.strptime(recordatorio["cuando"], "%Y-%m-%dT%H:%M")
            delta = datetime.timedelta(days=1 if rep == "diario" else 7)
            siguiente = (base + delta).strftime("%Y-%m-%dT%H:%M")
            c.execute(
                "INSERT INTO recordatorios (cuando, texto, repetir, dueno) "
                "VALUES (?, ?, ?, ?)",
                (siguiente, recordatorio["texto"], rep,
                 recordatorio.get("dueno") or DUENO_PRINCIPAL),
            )


# ----------------------------------------------------------- proyectos y fases
def add_proyecto(nombre, descripcion="", dueno=None):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO proyectos (nombre, descripcion, dueno) VALUES (?, ?, ?)",
            (nombre, descripcion, _d(dueno)))
        return cur.lastrowid


def _buscar_proyecto(c, nombre_o_id, dueno=None):
    """Busca un proyecto SIEMPRE dentro de los del dueno, para no mezclar
    datos de distintos usuarios (ni por nombre ni por id)."""
    d = _d(dueno)
    s = str(nombre_o_id).strip()
    if s.isdigit():
        r = c.execute("SELECT * FROM proyectos WHERE id=? AND dueno=?",
                      (int(s), d)).fetchone()
        if r:
            return r
    return c.execute("SELECT * FROM proyectos WHERE dueno=? AND LOWER(nombre) LIKE ?",
                     (d, f"%{s.lower()}%")).fetchone()


def add_fase(proyecto, titulo, contexto="", orden=None, minutos=None, dueno=None):
    d = _d(dueno)
    with conn() as c:
        p = _buscar_proyecto(c, proyecto, d)
        if not p:
            pid = c.execute("INSERT INTO proyectos (nombre, dueno) VALUES (?, ?)",
                            (str(proyecto), d)).lastrowid
        else:
            pid = p["id"]
        if orden is None:
            mx = c.execute("SELECT COALESCE(MAX(orden),0) FROM fases WHERE proyecto_id=?",
                           (pid,)).fetchone()[0]
            orden = mx + 1
        c.execute(
            "INSERT INTO fases (proyecto_id, orden, titulo, contexto, minutos) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, orden, titulo, contexto, minutos),
        )


def fase_actual(proyecto, dueno=None):
    """Primera fase pendiente (no hecha) del proyecto."""
    with conn() as c:
        p = _buscar_proyecto(c, proyecto, dueno)
        if not p:
            return None
        return c.execute(
            "SELECT * FROM fases WHERE proyecto_id=? AND hecho=0 ORDER BY orden LIMIT 1",
            (p["id"],)).fetchone()


def completar_fase(proyecto, dueno=None):
    """Marca como hecha la fase actual; devuelve (completada, siguiente)."""
    with conn() as c:
        p = _buscar_proyecto(c, proyecto, dueno)
        if not p:
            return None, None
        actual = c.execute(
            "SELECT * FROM fases WHERE proyecto_id=? AND hecho=0 ORDER BY orden LIMIT 1",
            (p["id"],)).fetchone()
        if not actual:
            return None, None
        c.execute("UPDATE fases SET hecho=1 WHERE id=?", (actual["id"],))
        siguiente = c.execute(
            "SELECT * FROM fases WHERE proyecto_id=? AND hecho=0 ORDER BY orden LIMIT 1",
            (p["id"],)).fetchone()
    return dict(actual), (dict(siguiente) if siguiente else None)


def cargar_proyectos(solo_pendientes=True, dueno=None):
    """Estructura de proyectos con sus fases, para mostrar o pasar a la IA."""
    out = []
    with conn() as c:
        for p in c.execute("SELECT * FROM proyectos WHERE dueno=? ORDER BY id",
                           (_d(dueno),)):
            q = "SELECT * FROM fases WHERE proyecto_id=?"
            if solo_pendientes:
                q += " AND hecho=0"
            q += " ORDER BY orden"
            fases = [
                {"orden": f["orden"], "titulo": f["titulo"],
                 "contexto": f["contexto"] or "", "hecho": bool(f["hecho"]),
                 "minutos": f["minutos"]}
                for f in c.execute(q, (p["id"],))
            ]
            out.append({"id": p["id"], "nombre": p["nombre"],
                        "descripcion": p["descripcion"] or "", "fases": fases})
    return out


# ------------------------------------------------------------------ intereses
def _clave_intereses(dueno=None):
    return "intereses:" + _d(dueno)


def get_intereses(dueno=None):
    """Lista de gustos/metas personales del usuario (para sugerencias)."""
    try:
        return json.loads(estado_get(_clave_intereses(dueno), "[]") or "[]")
    except Exception:
        return []


def add_interes(texto, dueno=None):
    lst = get_intereses(dueno)
    texto = texto.strip()
    if texto and texto.lower() not in (i.lower() for i in lst):
        lst.append(texto)
        estado_set(_clave_intereses(dueno), json.dumps(lst, ensure_ascii=False))
    return lst


def borrar_interes(objetivo, dueno=None):
    lst = get_intereses(dueno)
    objetivo = str(objetivo).strip()
    quitado = None
    if objetivo.isdigit():
        idx = int(objetivo) - 1
        if 0 <= idx < len(lst):
            quitado = lst.pop(idx)
    else:
        for i, t in enumerate(lst):
            if objetivo.lower() in t.lower():
                quitado = lst.pop(i)
                break
    if quitado is not None:
        estado_set(_clave_intereses(dueno), json.dumps(lst, ensure_ascii=False))
    return quitado


# --------------------------------------------------- recordatorios por id
def posponer_recordatorio(rid, minutos=30):
    nuevo = (datetime.datetime.now()
             + datetime.timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M")
    with conn() as c:
        c.execute("UPDATE recordatorios SET cuando=?, enviado=0 WHERE id=?",
                  (nuevo, rid))
    return nuevo


def borrar_recordatorio_id(rid):
    with conn() as c:
        r = c.execute("SELECT * FROM recordatorios WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        r = dict(r)
        if r.get("grupo"):
            r["borrados"] = _borrar_grupo(c, r["grupo"])
        else:
            c.execute("DELETE FROM recordatorios WHERE id=?", (rid,))
    return r


# --------------------------------------------------------------------- notas
def add_nota(texto, dueno=None):
    with conn() as c:
        c.execute("INSERT INTO notas (texto, fecha, dueno) VALUES (?, ?, ?)",
                  (texto, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   _d(dueno)))


def buscar_notas(objetivo="", dueno=None):
    q = "SELECT * FROM notas WHERE dueno=?"
    args = [_d(dueno)]
    if objetivo.strip():
        q += " AND LOWER(texto) LIKE ?"
        args.append(f"%{objetivo.strip().lower()}%")
    q += " ORDER BY id DESC LIMIT 15"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def borrar_nota(objetivo, dueno=None):
    notas = buscar_notas(str(objetivo) if not str(objetivo).isdigit() else "", dueno)
    objetivo = str(objetivo).strip()
    elegida = None
    if objetivo.isdigit():
        idx = int(objetivo) - 1
        if 0 <= idx < len(notas):
            elegida = notas[idx]
    elif notas:
        elegida = notas[0]
    if elegida:
        with conn() as c:
            c.execute("DELETE FROM notas WHERE id=?", (elegida["id"],))
    return elegida


# ------------------------------------------------------------------ lecturas
def set_lectura(nombre, marcador, dueno=None):
    with conn() as c:
        c.execute(
            "INSERT INTO lecturas (nombre, dueno, marcador, actualizado) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(nombre, dueno) DO UPDATE SET marcador=excluded.marcador, "
            "actualizado=excluded.actualizado",
            (nombre.strip().lower(), _d(dueno), marcador.strip(),
             datetime.date.today().isoformat()))


def get_lecturas(dueno=None):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lecturas WHERE dueno=? ORDER BY actualizado DESC",
            (_d(dueno),))]


# ------------------------------------------------------- actividad y racha
def log_actividad(tipo, texto="", dueno=None):
    with conn() as c:
        c.execute("INSERT INTO actividad (fecha, tipo, texto, dueno) VALUES (?, ?, ?, ?)",
                  (datetime.date.today().isoformat(), tipo, texto, _d(dueno)))


def actividad_de(fecha=None, dueno=None):
    f = (fecha or datetime.date.today()).isoformat()
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM actividad WHERE fecha=? AND dueno=?", (f, _d(dueno)))]


def racha(dueno=None):
    """Dias consecutivos (hasta hoy o ayer) con al menos una actividad."""
    with conn() as c:
        dias = {r["fecha"] for r in c.execute(
            "SELECT DISTINCT fecha FROM actividad WHERE dueno=?", (_d(dueno),))}
    d = datetime.date.today()
    if d.isoformat() not in dias:
        d -= datetime.timedelta(days=1)
    n = 0
    while d.isoformat() in dias:
        n += 1
        d -= datetime.timedelta(days=1)
    return n


def progreso_proyecto(proyecto, dueno=None):
    """(hechas, total) de fases del proyecto, o None."""
    with conn() as c:
        p = _buscar_proyecto(c, proyecto, dueno)
        if not p:
            return None
        total = c.execute("SELECT COUNT(*) FROM fases WHERE proyecto_id=?",
                          (p["id"],)).fetchone()[0]
        hechas = c.execute("SELECT COUNT(*) FROM fases WHERE proyecto_id=? AND hecho=1",
                           (p["id"],)).fetchone()[0]
    return hechas, total


# ---------------------------------------------------- historial conversacion
def historial_add(chat_id, rol, texto):
    with conn() as c:
        c.execute("INSERT INTO historial (chat_id, rol, texto, fecha) VALUES (?, ?, ?, ?)",
                  (str(chat_id), rol, texto[:1500],
                   datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")))
        c.execute("DELETE FROM historial WHERE chat_id=? AND id NOT IN "
                  "(SELECT id FROM historial WHERE chat_id=? ORDER BY id DESC LIMIT 30)",
                  (str(chat_id), str(chat_id)))


def historial_reciente(chat_id, n=10):
    with conn() as c:
        filas = c.execute(
            "SELECT rol, texto FROM historial WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), n)).fetchall()
    return [{"rol": r["rol"], "texto": r["texto"]} for r in reversed(filas)]


# ---------------------------------------------------------------- estado k/v
def estado_get(clave, defecto=None):
    with conn() as c:
        r = c.execute("SELECT valor FROM estado WHERE clave=?", (clave,)).fetchone()
    return r["valor"] if r else defecto


def estado_set(clave, valor):
    with conn() as c:
        c.execute("INSERT INTO estado (clave, valor) VALUES (?, ?) "
                  "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                  (clave, str(valor)))


# ------------------------------------------------------------- uso de la IA
def uso_inc():
    """Cuenta una llamada a Gemini en el dia de hoy."""
    clave = "uso_" + datetime.date.today().isoformat()
    actual = int(estado_get(clave, 0) or 0)
    estado_set(clave, actual + 1)


def uso_resumen(dias=7):
    """[(fecha, llamadas)] de los ultimos N dias, el mas reciente primero."""
    out = []
    hoy = datetime.date.today()
    for i in range(dias):
        f = (hoy - datetime.timedelta(days=i)).isoformat()
        out.append((f, int(estado_get("uso_" + f, 0) or 0)))
    return out


# ------------------------------------------------------------------ respaldo
def respaldo_diario():
    """Copia agenda.db a respaldos/ una vez al dia; conserva los ultimos 7."""
    hoy = datetime.date.today().isoformat()
    if estado_get("ultimo_respaldo") == hoy:
        return False
    carpeta = os.path.join(BASE_DIR, "respaldos")
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f"agenda-{hoy}.db")
    with conn() as c:
        respaldo = sqlite3.connect(destino)
        try:
            c.backup(respaldo)
        finally:
            respaldo.close()
    viejos = sorted(f for f in os.listdir(carpeta)
                    if f.startswith("agenda-") and f.endswith(".db"))
    for f in viejos[:-7]:
        os.remove(os.path.join(carpeta, f))
    estado_set("ultimo_respaldo", hoy)
    return True


if __name__ == "__main__":
    init_db()
    print("Base de datos lista en", DB_PATH)
