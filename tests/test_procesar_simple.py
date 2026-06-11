# -*- coding: utf-8 -*-
"""Tests del parser por reglas (bot.procesar_simple), sin red ni IA.

Usa una BD temporal porque algunas ramas (recordatorios, fases) tocan db.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import bot  # noqa: E402


class TestProcesarSimple(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        self.tareas = {"pendientes": [], "eventos": []}

    def tearDown(self):
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            p = self.ruta + suf
            if os.path.exists(p):
                os.remove(p)

    def test_agregar_pendiente(self):
        msg, cambio = bot.procesar_simple("agrega comprar pan", self.tareas)
        self.assertTrue(cambio)
        self.assertIn("comprar pan", self.tareas["pendientes"])

    def test_borrar_pendiente(self):
        self.tareas["pendientes"].append("comprar pan")
        msg, cambio = bot.procesar_simple("borra comprar pan", self.tareas)
        self.assertTrue(cambio)
        self.assertEqual(self.tareas["pendientes"], [])

    def test_evento_explicito(self):
        msg, cambio = bot.procesar_simple(
            "evento 2026-06-20 18:30 cumple", self.tareas)
        self.assertTrue(cambio)
        ev = self.tareas["eventos"][0]
        self.assertEqual(ev["fecha"], "2026-06-20")
        self.assertEqual(ev["hora"], "18:30")
        self.assertEqual(ev["titulo"], "cumple")

    def test_recordatorio_se_guarda_en_db(self):
        msg, cambio = bot.procesar_simple(
            "recuerdame llamar al banco mañana a las 10am", self.tareas)
        self.assertFalse(cambio)  # los recordatorios no tocan tareas
        self.assertEqual(len(db.listar_recordatorios()), 1)

    def test_html_escapado_en_pendiente(self):
        msg, _ = bot.procesar_simple("agrega <script> & cosas", self.tareas)
        self.assertNotIn("<script>", msg)
        self.assertIn("&lt;script&gt;", msg)

    def test_estricto_devuelve_none_si_no_entiende(self):
        self.assertIsNone(
            bot.procesar_simple("xyz frase rara sin sentido", self.tareas, estricto=True))

    def test_no_estricto_responde_ayuda(self):
        msg, cambio = bot.procesar_simple("xyz frase rara", self.tareas)
        self.assertFalse(cambio)
        self.assertIn("No entendi", msg)


if __name__ == "__main__":
    unittest.main()
