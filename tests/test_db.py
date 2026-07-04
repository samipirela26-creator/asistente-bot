# -*- coding: utf-8 -*-
"""Tests de la capa de base de datos (db.py) sobre una BD temporal.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
import datetime

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import db  # noqa: E402


class BaseDB(unittest.TestCase):
    def setUp(self):
        # Cada test usa su propia BD temporal para no tocar la real.
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()

    def tearDown(self):
        db.set_dueno(db.DUENO_PRINCIPAL)  # no contamines otros tests
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            p = self.ruta + suf
            if os.path.exists(p):
                os.remove(p)


class TestEstadoKV(BaseDB):
    def test_get_defecto(self):
        self.assertIsNone(db.estado_get("inexistente"))
        self.assertEqual(db.estado_get("inexistente", "x"), "x")

    def test_set_y_get(self):
        db.estado_set("k", "v")
        self.assertEqual(db.estado_get("k"), "v")

    def test_set_sobrescribe(self):
        db.estado_set("k", "1")
        db.estado_set("k", "2")
        self.assertEqual(db.estado_get("k"), "2")


class TestTareas(BaseDB):
    def test_guardar_y_cargar(self):
        db.guardar_tareas({
            "pendientes": ["comprar pan"],
            "eventos": [{"fecha": "2026-06-12", "hora": "10:00", "titulo": "dentista"}],
        })
        t = db.cargar_tareas()
        self.assertEqual(t["pendientes"], ["comprar pan"])
        self.assertEqual(len(t["eventos"]), 1)
        self.assertEqual(t["eventos"][0]["titulo"], "dentista")
        self.assertEqual(t["eventos"][0]["hora"], "10:00")

    def test_evento_sin_hora(self):
        db.guardar_tareas({"eventos": [{"fecha": "2026-06-12", "titulo": "x"}]})
        ev = db.cargar_tareas()["eventos"][0]
        self.assertNotIn("hora", ev)


class TestRecordatorios(BaseDB):
    def test_add_y_vencidos(self):
        db.add_recordatorio("2026-06-11T08:00", "tomar agua")
        ahora = datetime.datetime(2026, 6, 11, 9, 0)
        venc = db.recordatorios_vencidos(ahora)
        self.assertEqual(len(venc), 1)
        self.assertEqual(venc[0]["texto"], "tomar agua")

    def test_no_vencido_futuro(self):
        db.add_recordatorio("2026-06-11T10:00", "futuro")
        ahora = datetime.datetime(2026, 6, 11, 9, 0)
        self.assertEqual(db.recordatorios_vencidos(ahora), [])

    def test_borrar_por_texto(self):
        db.add_recordatorio("2026-06-11T08:00", "pagar luz")
        q = db.borrar_recordatorio("luz")
        self.assertIsNotNone(q)
        self.assertEqual(db.listar_recordatorios(), [])

    def test_repeticion_crea_siguiente(self):
        db.add_recordatorio("2026-06-11T07:00", "pastilla", repetir="diario")
        rec = db.listar_recordatorios()[0]
        db.marcar_enviado(rec)
        pend = db.listar_recordatorios()
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["cuando"], "2026-06-12T07:00")


class TestProyectos(BaseDB):
    def test_fases_y_completar(self):
        db.add_fase("casa", "limpiar")
        db.add_fase("casa", "pintar")
        actual = db.fase_actual("casa")
        self.assertEqual(actual["titulo"], "limpiar")
        comp, sig = db.completar_fase("casa")
        self.assertEqual(comp["titulo"], "limpiar")
        self.assertEqual(sig["titulo"], "pintar")
        self.assertEqual(db.progreso_proyecto("casa"), (1, 2))

    def test_devolver_avance(self):
        db.add_fase("casa", "limpiar")
        comp, _ = db.completar_fase("casa")
        db.log_actividad("fase", comp["titulo"])  # como hace el bot al avanzar
        self.assertEqual(db.progreso_proyecto("casa"), (1, 1))
        self.assertEqual(len(db.actividad_de()), 1)
        f = db.descompletar_fase(comp["id"])
        self.assertEqual(f["titulo"], "limpiar")
        # La fase vuelve a pendiente y el avance se borra de la actividad.
        self.assertEqual(db.progreso_proyecto("casa"), (0, 1))
        self.assertEqual(db.actividad_de(), [])
        # Devolver dos veces no revienta ni hace nada.
        self.assertIsNone(db.descompletar_fase(comp["id"]))


class TestIntereses(BaseDB):
    def test_add_y_borrar(self):
        db.add_interes("leer")
        db.add_interes("correr")
        self.assertEqual(db.get_intereses(), ["leer", "correr"])
        db.add_interes("leer")  # duplicado, no se repite
        self.assertEqual(len(db.get_intereses()), 2)
        db.borrar_interes("correr")
        self.assertEqual(db.get_intereses(), ["leer"])


class TestAislamientoMultiusuario(BaseDB):
    """Cada dueño ve SOLO sus datos; nada se filtra entre usuarios."""

    def test_tareas_aisladas(self):
        db.set_dueno("ana")
        db.guardar_tareas({"pendientes": ["pan de ana"], "eventos": []})
        db.set_dueno("beto")
        db.guardar_tareas({"pendientes": ["pan de beto"], "eventos": []})
        db.set_dueno("ana")
        self.assertEqual(db.cargar_tareas()["pendientes"], ["pan de ana"])
        db.set_dueno("beto")
        self.assertEqual(db.cargar_tareas()["pendientes"], ["pan de beto"])

    def test_recordatorios_aislados(self):
        db.add_recordatorio("2026-06-11T08:00", "rec de ana", dueno="ana")
        db.add_recordatorio("2026-06-11T08:00", "rec de beto", dueno="beto")
        self.assertEqual([r["texto"] for r in db.listar_recordatorios("ana")],
                         ["rec de ana"])
        # El barrido global (hilo de recordatorios) los ve todos, con su dueño.
        ahora = datetime.datetime(2026, 6, 11, 9, 0)
        venc = db.recordatorios_vencidos(ahora)  # dueno=None -> todos
        self.assertEqual({r["dueno"] for r in venc}, {"ana", "beto"})

    def test_proyectos_aislados(self):
        db.set_dueno("ana")
        db.add_fase("casa", "limpiar")
        db.set_dueno("beto")
        self.assertEqual(db.cargar_proyectos(), [])  # beto no ve la casa de ana
        self.assertIsNone(db.fase_actual("casa"))

    def test_intereses_aislados(self):
        db.add_interes("guitarra", dueno="ana")
        self.assertEqual(db.get_intereses("ana"), ["guitarra"])
        self.assertEqual(db.get_intereses("beto"), [])

    def test_lecturas_misma_clave_distinto_dueno(self):
        db.set_lectura("biblia", "Juan 5", dueno="ana")
        db.set_lectura("biblia", "Genesis 1", dueno="beto")
        self.assertEqual(db.get_lecturas("ana")[0]["marcador"], "Juan 5")
        self.assertEqual(db.get_lecturas("beto")[0]["marcador"], "Genesis 1")


if __name__ == "__main__":
    unittest.main()
