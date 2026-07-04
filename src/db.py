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

# db.py vive en src/; los datos (agenda.db, tareas.json, respaldos/) viven un
# nivel arriba, en la raiz del repo -- de ahi el dirname() doble.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "agenda.db")
JSON_VIEJO = os.path.join(BASE_DIR, "tareas.json")

# --- Multiusuario: cada cuenta tiene sus propios datos aislados. ---
# El "dueno" identifica de quien son los datos. Las cuentas personales del
# config comparten el dueno DUENO_PRINCIPAL (asi heredan lo que ya existia);
# cualquier otro usuario nuevo usa su propio chat_id como dueno.
DUENO_PRINCIPAL = "principal"
_local = threading.local()

# Franja de "silencio" nocturno: las RE-insistencias automáticas que caerían de
# madrugada se posponen hasta SILENCIO_FIN. NO afecta a la primera entrega de un
# recordatorio (esa siempre suena a su hora, aunque sea de noche): solo evita que
# un aviso se repita una y otra vez pasada la medianoche.
SILENCIO = (23, 7)  # (hora_inicio, hora_fin) -> [23:00, 07:00)


def _en_silencio(dt, silencio=SILENCIO):
    ini, fin = silencio
    h = dt.hour
    if ini < fin:
        return ini <= h < fin
    return h >= ini or h < fin  # cruza medianoche


def _sacar_de_silencio(dt, silencio=SILENCIO):
    """Si 'dt' cae en la franja de silencio, lo mueve a la hora de fin de
    silencio (mañana). Si no, lo deja igual."""
    if not _en_silencio(dt, silencio):
        return dt
    fin = silencio[1]
    objetivo = dt.replace(hour=fin, minute=0, second=0, microsecond=0)
    if objetivo <= dt:
        objetivo += datetime.timedelta(days=1)
    return objetivo


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


# Version actual del esquema. SUBE este numero cada vez que agregues una
# migracion nueva en _aplicar_migraciones (y agrega el bloque correspondiente).
# Historial:
#   1 = base + insistir_min/grupo/insistir_veces, fases.minutos, columna dueno
#       multiusuario y reconstruccion de 'lecturas' con PK (nombre, dueno).
#   2 = recordatorios.hora_explicita: marca si el usuario fijo la hora a
#       proposito. Si es 0, una entrega que caiga de madrugada se difiere a la
#       manana (evita avisos sorpresa a las 2am que nadie pidio).
#   3 = actividad.categoria: etiqueta libre (la IA la elige al registrar un
#       avance nocturno; una fase completada usa el nombre de su proyecto).
SCHEMA_VERSION = 3


def schema_version():
    """Version del esquema aplicada a la BD actual (PRAGMA user_version)."""
    with conn() as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _aplicar_migraciones(c, desde):
    """Aplica, EN ORDEN, las migraciones cuya version sea mayor que 'desde'.
    Cada paso es idempotente (revisa antes de alterar) para que sea seguro
    incluso sobre una BD que ya tenia los cambios de una version anterior del
    codigo no-versionada. 'c' es una conexion abierta dentro de una transaccion."""
    if desde < 1:
        # --- Migracion 1: columnas extra y modelo multiusuario ---
        cols = [r[1] for r in c.execute("PRAGMA table_info(recordatorios)")]
        if "insistir_min" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN insistir_min INTEGER")
        if "grupo" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN grupo TEXT")
        if "insistir_veces" not in cols:
            c.execute("ALTER TABLE recordatorios ADD COLUMN insistir_veces INTEGER DEFAULT 0")
        cols_f = [r[1] for r in c.execute("PRAGMA table_info(fases)")]
        if "minutos" not in cols_f:
            c.execute("ALTER TABLE fases ADD COLUMN minutos INTEGER")
        for tabla in ("pendientes", "eventos", "recordatorios", "proyectos",
                      "notas", "actividad"):
            cols_t = [r[1] for r in c.execute(f"PRAGMA table_info({tabla})")]
            if "dueno" not in cols_t:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN dueno TEXT")
            c.execute(f"UPDATE {tabla} SET dueno=? WHERE dueno IS NULL",
                      (DUENO_PRINCIPAL,))
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
    if desde < 2:
        # --- Migracion 2: marca de hora puesta a proposito por el usuario ---
        cols = [r[1] for r in c.execute("PRAGMA table_info(recordatorios)")]
        if "hora_explicita" not in cols:
            c.execute("ALTER TABLE recordatorios "
                      "ADD COLUMN hora_explicita INTEGER DEFAULT 0")
    if desde < 3:
        # --- Migracion 3: categoria del avance ---
        cols = [r[1] for r in c.execute("PRAGMA table_info(actividad)")]
        if "categoria" not in cols:
            c.execute("ALTER TABLE actividad ADD COLUMN categoria TEXT")
    # Migracion 4 (futura): añade aqui un bloque `if desde < 4:` y sube
    # SCHEMA_VERSION a 4. No reordenes ni borres los bloques anteriores.


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
            CREATE TABLE IF NOT EXISTS papelera (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                dueno  TEXT NOT NULL,
                creado TEXT NOT NULL,   -- ISO: snapshot de un 'borrar todo'
                datos  TEXT NOT NULL    -- JSON con las filas borradas
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id      TEXT PRIMARY KEY,  -- todo el que ha escrito al bot
                primer_visto TEXT,
                ultimo_visto TEXT,
                partes       INTEGER            -- NULL=sin preguntar, 0=no, 1=si
            );
            """
        )
        # Esquema VERSIONADO: las migraciones se aplican en orden y una sola vez.
        # SQLite recuerda la version aplicada en 'PRAGMA user_version', asi que
        # en cada arranque normal NO se vuelve a inspeccionar/alterar nada (es
        # mas rapido y deja un registro claro de la evolucion del esquema).
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            _aplicar_migraciones(c, version)
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
                     grupo=None, dueno=None, insistir_veces=0,
                     hora_explicita=False):
    """Crea un recordatorio y devuelve su id.

    hora_explicita=True cuando el usuario fijo la hora a proposito (p.ej. dijo
    'a las 2 de la mañana'): en ese caso suena a su hora aunque sea de noche.
    Si es False y cae en la franja de silencio, 'posponer_madrugada' lo movera
    a la mañana antes de entregarlo."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO recordatorios (cuando, texto, repetir, insistir_min, "
            "grupo, dueno, insistir_veces, hora_explicita) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cuando_iso, texto, repetir, insistir_min, grupo, _d(dueno),
             insistir_veces, 1 if hora_explicita else 0),
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


def silenciar_recordatorio(rec_id, dueno=None):
    """Calla un recordatorio (lo saca de pendientes) sin borrarlo del historial.
    Sirve para 'pausa / yo te aviso': corta la insistencia automatica al instante,
    a diferencia de borrar_recordatorio que lo elimina. Devuelve True si lo halló."""
    with conn() as c:
        cur = c.execute(
            "UPDATE recordatorios SET enviado=1, insistir_veces=0 "
            "WHERE id=? AND dueno=?",
            (rec_id, _d(dueno)),
        )
        return cur.rowcount > 0


# --------------------------------------------------- borron total recuperable
# Tablas con una columna 'dueno' que entran al borron total. 'fases' NO tiene
# dueno (cuelga de un proyecto), por eso se trata aparte vía proyecto_id.
_TABLAS_USUARIO = ("pendientes", "eventos", "recordatorios",
                   "proyectos", "notas", "lecturas")
PAPELERA_HORAS = 24   # cuanto tiempo se puede deshacer un 'borrar todo'


def _purgar_papelera(c, ahora=None):
    """Borra snapshots de la papelera mas viejos que PAPELERA_HORAS."""
    ahora = ahora or datetime.datetime.now()
    limite = (ahora - datetime.timedelta(hours=PAPELERA_HORAS)).isoformat()
    c.execute("DELETE FROM papelera WHERE creado < ?", (limite,))


def contar_usuarios():
    """Cuenta cuantos 'dueños' distintos tienen datos en la BD. El dueño
    'principal' eres TÚ (tus cuentas personales comparten ese espacio); el resto
    son usuarios externos con datos aislados. Devuelve:
        {'total': N, 'externos': [dueno, ...], 'tiene_principal': bool}"""
    duenos = set()
    with conn() as c:
        for t in _TABLAS_USUARIO + ("actividad",):
            try:
                for r in c.execute(f"SELECT DISTINCT dueno FROM {t}"):
                    if r[0]:
                        duenos.add(r[0])
            except sqlite3.Error:
                pass
    externos = sorted(d for d in duenos if d != DUENO_PRINCIPAL)
    return {
        "total": len(duenos),
        "externos": externos,
        "tiene_principal": DUENO_PRINCIPAL in duenos,
    }


# --------------------------------------------------- registro de TODOS los chats
# La tabla 'usuarios' anota a CUALQUIERA que escriba al bot, aunque no cree ni una
# tarea (contar_usuarios solo veia a quien tenia datos; asi se nos escapaban los
# que solo conversan). Tambien guarda si quiere el parte matutino/nocturno.
def registrar_visto(chat_id):
    """Anota (o actualiza) a un chat que acaba de escribir. Idempotente."""
    cid = str(chat_id)
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            "INSERT INTO usuarios (chat_id, primer_visto, ultimo_visto) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET ultimo_visto=?",
            (cid, ahora, ahora, ahora))


def get_partes(chat_id):
    """¿Quiere este chat el parte automatico? 1=si, 0=no, None=sin preguntar."""
    with conn() as c:
        r = c.execute("SELECT partes FROM usuarios WHERE chat_id=?",
                      (str(chat_id),)).fetchone()
    return None if r is None or r[0] is None else int(r[0])


def set_partes(chat_id, quiere):
    """Guarda la preferencia de partes automaticos de un chat (registrandolo
    de paso si era la primera vez)."""
    cid = str(chat_id)
    val = 1 if quiere else 0
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            "INSERT INTO usuarios (chat_id, primer_visto, ultimo_visto, partes) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET partes=?",
            (cid, ahora, ahora, val, val))


def usuarios_con_partes():
    """Chats que pidieron el parte automatico (partes=1)."""
    with conn() as c:
        return [r[0] for r in c.execute(
            "SELECT chat_id FROM usuarios WHERE partes=1 ORDER BY chat_id")]


def usuarios_registrados():
    """Todos los chats que han escrito al bot, con su preferencia de partes.
    Devuelve dicts {chat_id, primer_visto, ultimo_visto, partes}."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT chat_id, primer_visto, ultimo_visto, partes "
            "FROM usuarios ORDER BY ultimo_visto DESC")]


def borrar_todo(dueno=None, ahora=None):
    """Borra TODOS los datos del dueno (tareas, eventos, recordatorios, notas,
    proyectos, fases, lecturas) PERO guarda antes un snapshot en 'papelera' para
    poder deshacer durante PAPELERA_HORAS. Devuelve (total_borrado, papelera_id);
    (0, None) si no habia nada que borrar."""
    d = _d(dueno)
    ahora = ahora or datetime.datetime.now()
    snapshot, total = {}, 0
    with conn() as c:
        for t in _TABLAS_USUARIO:
            filas = [dict(r) for r in c.execute(
                f"SELECT * FROM {t} WHERE dueno=?", (d,))]
            snapshot[t] = filas
            total += len(filas)
        snapshot["fases"] = [dict(r) for r in c.execute(
            "SELECT f.* FROM fases f JOIN proyectos p ON f.proyecto_id=p.id "
            "WHERE p.dueno=?", (d,))]
        total += len(snapshot["fases"])
        if total == 0:
            return (0, None)
        cur = c.execute(
            "INSERT INTO papelera (dueno, creado, datos) VALUES (?, ?, ?)",
            (d, ahora.isoformat(timespec="seconds"),
             json.dumps(snapshot, ensure_ascii=False)))
        pid = cur.lastrowid
        # Fases primero (cuelgan de proyectos), luego el resto.
        c.execute("DELETE FROM fases WHERE proyecto_id IN "
                  "(SELECT id FROM proyectos WHERE dueno=?)", (d,))
        for t in _TABLAS_USUARIO:
            c.execute(f"DELETE FROM {t} WHERE dueno=?", (d,))
        _purgar_papelera(c, ahora)
        return (total, pid)


def recuperar_todo(dueno=None, papelera_id=None, ahora=None):
    """Restaura el ultimo borron del dueno si esta dentro de PAPELERA_HORAS
    (o el snapshot 'papelera_id' concreto). Devuelve el total restaurado, o None
    si no hay nada recuperable. Tras restaurar, consume el snapshot."""
    d = _d(dueno)
    ahora = ahora or datetime.datetime.now()
    limite = (ahora - datetime.timedelta(hours=PAPELERA_HORAS)).isoformat()
    with conn() as c:
        if papelera_id is not None:
            row = c.execute(
                "SELECT * FROM papelera WHERE id=? AND dueno=? AND creado>=?",
                (papelera_id, d, limite)).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM papelera WHERE dueno=? AND creado>=? "
                "ORDER BY id DESC LIMIT 1", (d, limite)).fetchone()
        if not row:
            return None
        snapshot = json.loads(row["datos"])
        total = 0
        for t in list(_TABLAS_USUARIO) + ["fases"]:
            for fila in snapshot.get(t, []):
                cols = list(fila.keys())
                marcas = ",".join("?" for _ in cols)
                c.execute(
                    f"INSERT INTO {t} ({','.join(cols)}) VALUES ({marcas})",
                    [fila[k] for k in cols])
                total += 1
        c.execute("DELETE FROM papelera WHERE id=?", (row["id"],))
        return total


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


def posponer_madrugada(silencio=SILENCIO, ahora=None):
    """Mueve a la mañana (hora de fin de silencio) las entregas pendientes que
    caigan en la franja de madrugada y NO tengan la hora puesta a proposito
    (hora_explicita=0). Evita avisos sorpresa de madrugada que el usuario no
    pidio. Lo llama el hilo de recordatorios antes de barrer los vencidos.
    Devuelve cuantos recordatorios re-agendó."""
    if ahora is None:
        ahora = datetime.datetime.now()
    movidos = 0
    with conn() as c:
        pend = c.execute(
            "SELECT id, cuando FROM recordatorios "
            "WHERE enviado=0 AND hora_explicita=0 AND cuando<=?",
            (ahora.strftime("%Y-%m-%dT%H:%M"),)).fetchall()
        for r in pend:
            try:
                cuando = datetime.datetime.strptime(r["cuando"], "%Y-%m-%dT%H:%M")
            except (ValueError, TypeError):
                continue
            if not _en_silencio(cuando, silencio):
                continue
            destino = _sacar_de_silencio(ahora, silencio)
            c.execute("UPDATE recordatorios SET cuando=? WHERE id=?",
                      (destino.strftime("%Y-%m-%dT%H:%M"), r["id"]))
            movidos += 1
    return movidos


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


def marcar_enviado(recordatorio, silencio=SILENCIO):
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
        # Insistencia a pedido: vuelve a avisar solo las veces que el usuario
        # eligió por botones (veces>0, descontando) o "hasta que lo marque
        # hecho" (veces==-1, súper insistente). La PRIMERA vez siempre suena a
        # su hora; solo las RE-insistencias evitan la madrugada (silencio).
        if insistir and (veces > 0 or veces == -1):
            siguiente = _sacar_de_silencio(
                ahora + datetime.timedelta(minutes=int(insistir)), silencio)
            quedan = veces if veces == -1 else veces - 1
            c.execute("UPDATE recordatorios SET cuando=?, insistir_veces=? WHERE id=?",
                      (siguiente.strftime("%Y-%m-%dT%H:%M"), quedan, recordatorio["id"]))
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


def descompletar_fase(fase_id, dueno=None):
    """Devuelve una fase ya marcada como hecha al estado pendiente (deshacer un
    avance). Tambien borra el ultimo registro de actividad 'fase' con ese
    titulo del dia de hoy para no inflar la racha. Devuelve la fase o None."""
    with conn() as c:
        f = c.execute(
            "SELECT fa.* FROM fases fa JOIN proyectos p ON fa.proyecto_id=p.id "
            "WHERE fa.id=? AND p.dueno=? AND fa.hecho=1", (fase_id, _d(dueno))
        ).fetchone()
        if not f:
            return None
        c.execute("UPDATE fases SET hecho=0 WHERE id=?", (fase_id,))
        c.execute(
            "DELETE FROM actividad WHERE rowid IN (SELECT rowid FROM actividad "
            "WHERE tipo='fase' AND texto=? AND fecha=? AND dueno=? ORDER BY "
            "rowid DESC LIMIT 1)",
            (f["titulo"], datetime.date.today().isoformat(), _d(dueno)))
    return dict(f)


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


# ------------------------------------------------------ nombre del usuario
def _clave_nombre(dueno=None):
    return "nombre:" + _d(dueno)


def _clave_trato(dueno=None):
    return "trato_n:" + _d(dueno)


def _clave_titulo(dueno=None):
    return "trato_titulo:" + _d(dueno)


TITULOS_VALIDOS = ("señor", "señora", "señorita")


def get_titulo(dueno=None):
    """Tratamiento elegido por el usuario: 'señor' | 'señora' | 'señorita'.
    Por defecto 'señor' (hasta que elija)."""
    t = (estado_get(_clave_titulo(dueno)) or "").strip().lower()
    return t if t in TITULOS_VALIDOS else "señor"


def set_titulo(titulo, dueno=None):
    """Guarda el tratamiento. Acepta el nombre completo o un codigo corto
    (sr/sra/srta). Si no es valido, queda en 'señor'."""
    mapa = {"sr": "señor", "sra": "señora", "srta": "señorita"}
    t = (titulo or "").strip().lower()
    t = mapa.get(t, t)
    if t not in TITULOS_VALIDOS:
        t = "señor"
    estado_set(_clave_titulo(dueno), t)
    return t


def get_nombre(dueno=None):
    """Nombre con el que el usuario pidio que se le llame, o None si aun no lo
    ha indicado."""
    n = estado_get(_clave_nombre(dueno))
    return n or None


def set_nombre(nombre, dueno=None):
    """Guarda el nombre del usuario (recortado a 40 caracteres por prudencia)."""
    estado_set(_clave_nombre(dueno), (nombre or "").strip()[:40])


def tratamiento(dueno=None, avanzar=True):
    """Forma de dirigirse al usuario. Alterna, en llamadas sucesivas, entre
    '<titulo> <Nombre>' y el '<titulo>' a secas (señor/señora/señorita segun lo
    que el usuario eligio), para que suene mas natural y elegante. Si aun no hay
    nombre, devuelve solo el titulo.

    avanzar=False solo consulta el trato actual sin mover el turno (util para
    tests o para mostrarlo sin gastar el ciclo)."""
    titulo = get_titulo(dueno)
    nombre = get_nombre(dueno)
    if not nombre:
        return titulo
    n = int(estado_get(_clave_trato(dueno), 0) or 0)
    if avanzar:
        estado_set(_clave_trato(dueno), n + 1)
    return f"{titulo} {nombre}" if n % 2 == 0 else titulo


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
def log_actividad(tipo, texto="", dueno=None, categoria=None):
    with conn() as c:
        c.execute("INSERT INTO actividad (fecha, tipo, texto, dueno, categoria) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (datetime.date.today().isoformat(), tipo, texto, _d(dueno), categoria))


def actividad_de(fecha=None, dueno=None):
    f = (fecha or datetime.date.today()).isoformat()
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM actividad WHERE fecha=? AND dueno=?", (f, _d(dueno)))]


def categorias_recientes(dueno=None, dias=30):
    """Nombres de categoria usados en los ultimos 'dias', mas frecuentes primero.
    Sirve de pista para que la IA reutilice nombres en vez de inventar variantes."""
    desde = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
    with conn() as c:
        filas = c.execute(
            "SELECT categoria, COUNT(*) AS n FROM actividad "
            "WHERE fecha >= ? AND dueno=? AND categoria IS NOT NULL AND categoria != '' "
            "GROUP BY categoria ORDER BY n DESC LIMIT 10",
            (desde, _d(dueno)))
        return [r["categoria"] for r in filas]


def actividad_por_categoria(desde, hasta, dueno=None):
    """Conteo de avances entre 'desde' y 'hasta' (date, inclusive), agrupados
    por categoria. Sin categoria asignada cae bajo 'Sin categoria'."""
    with conn() as c:
        filas = c.execute(
            "SELECT COALESCE(NULLIF(categoria, ''), 'Sin categoria') AS cat, "
            "COUNT(*) AS n FROM actividad WHERE fecha BETWEEN ? AND ? AND dueno=? "
            "GROUP BY cat ORDER BY n DESC",
            (desde.isoformat(), hasta.isoformat(), _d(dueno)))
        return [(r["cat"], r["n"]) for r in filas]


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


# --------------------------------------------------- horario de la tarjeta
def _clave_horario_tarjeta(dueno=None):
    return "tarjeta_horario:" + _d(dueno)


def _clave_tarjeta_enviada(dueno=None):
    return "tarjeta_enviada:" + _d(dueno)


def get_horario_tarjeta(dueno=None):
    """(dia_semana, 'HH:MM') elegido por el usuario para la tarjeta semanal.
    dia_semana: 0=lunes .. 6=domingo. Por defecto: domingo a las 09:00."""
    v = estado_get(_clave_horario_tarjeta(dueno))
    if not v or "|" not in v:
        return 6, "09:00"
    dia, hora = v.split("|", 1)
    try:
        return int(dia), hora
    except ValueError:
        return 6, "09:00"


def set_horario_tarjeta(dia, hora, dueno=None):
    estado_set(_clave_horario_tarjeta(dueno), f"{dia}|{hora}")


def tarjeta_pendiente_hoy(dueno=None, ahora=None):
    """True si YA toca enviar la tarjeta segun el horario configurado y
    todavia no se envio hoy. La ventana es de 1h para tolerar que el timer
    de sondeo (cada 15 min) no coincida al segundo con la hora elegida."""
    ahora = ahora or datetime.datetime.now()
    dia, hora = get_horario_tarjeta(dueno)
    if ahora.weekday() != dia:
        return False
    try:
        h, m = (int(x) for x in hora.split(":"))
    except ValueError:
        h, m = 9, 0
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if not (objetivo <= ahora <= objetivo + datetime.timedelta(hours=1)):
        return False
    return estado_get(_clave_tarjeta_enviada(dueno)) != ahora.date().isoformat()


def marcar_tarjeta_enviada(dueno=None, ahora=None):
    ahora = ahora or datetime.datetime.now()
    estado_set(_clave_tarjeta_enviada(dueno), ahora.date().isoformat())


# ------------------------------------------------------------- uso de la IA
def uso_inc():
    """Cuenta una llamada a Gemini en el dia de hoy. La suma la hace SQLite de
    forma ATOMICA para que dos hilos (p.ej. el principal y el de recordatorios)
    no se pisen y pierdan cuentas."""
    clave = "uso_" + datetime.date.today().isoformat()
    with conn() as c:
        c.execute(
            "INSERT INTO estado (clave, valor) VALUES (?, '1') "
            "ON CONFLICT(clave) DO UPDATE SET valor = CAST(valor AS INTEGER) + 1",
            (clave,))


def uso_resumen(dias=7):
    """[(fecha, llamadas)] de los ultimos N dias, el mas reciente primero."""
    out = []
    hoy = datetime.date.today()
    for i in range(dias):
        f = (hoy - datetime.timedelta(days=i)).isoformat()
        out.append((f, int(estado_get("uso_" + f, 0) or 0)))
    return out


# ------------------------------------------------------- metricas / observabilidad
# Contadores acumulados desde que arranco el bot por primera vez (viven en la
# tabla 'estado' con prefijo 'metrica:'). Sirven para ver de un vistazo cuanto
# se usa el bot y cuanto falla la IA, sin montar Prometheus ni nada externo.
_PREFIJO_METRICA = "metrica:"


def metrica_inc(nombre, n=1):
    """Suma n a un contador de forma ATOMICA (a prueba de hilos: la suma la hace
    SQLite, no Python, asi dos hilos no se pisan)."""
    clave = _PREFIJO_METRICA + nombre
    with conn() as c:
        c.execute(
            "INSERT INTO estado (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET "
            "valor = CAST(valor AS INTEGER) + ?",
            (clave, str(int(n)), int(n)))


def metrica_observar(nombre, ms):
    """Registra una latencia (ms): acumula suma y cuenta para sacar el promedio.
    Guarda dos contadores: '<nombre>_ms_suma' y '<nombre>_n'."""
    metrica_inc(nombre + "_ms_suma", int(ms))
    metrica_inc(nombre + "_n", 1)


def metricas():
    """Devuelve {nombre: valor} de todos los contadores. Para latencias agrega
    '<nombre>_ms_prom' (promedio en ms) calculado al vuelo."""
    out = {}
    with conn() as c:
        filas = c.execute(
            "SELECT clave, valor FROM estado WHERE clave LIKE ?",
            (_PREFIJO_METRICA + "%",)).fetchall()
    for f in filas:
        nombre = f["clave"][len(_PREFIJO_METRICA):]
        try:
            out[nombre] = int(f["valor"])
        except (TypeError, ValueError):
            out[nombre] = f["valor"]
    # Promedios de latencia donde haya suma + n.
    for clave in list(out):
        if clave.endswith("_ms_suma"):
            base = clave[:-len("_ms_suma")]
            n = out.get(base + "_n", 0)
            if n:
                out[base + "_ms_prom"] = round(out[clave] / n)
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
