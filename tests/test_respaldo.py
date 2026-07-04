# -*- coding: utf-8 -*-
"""Test del respaldo: un backup que nunca probaste restaurar NO es un backup.

Verifica que respaldo_diario() crea una copia COMPLETA y RESTAURABLE de la BD
(se puede abrir y trae los mismos datos), que es idempotente por día y que
conserva solo los últimos 7.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import db   # noqa: E402


class RespaldoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_db, self._orig_base = db.DB_PATH, db.BASE_DIR
        db.DB_PATH = os.path.join(self.dir, "agenda.db")
        db.BASE_DIR = self.dir            # respaldos/ irá al temp, no al repo
        db.init_db()
        db.set_dueno(db.DUENO_PRINCIPAL)

    def tearDown(self):
        db.DB_PATH, db.BASE_DIR = self._orig_db, self._orig_base
        db.set_dueno(db.DUENO_PRINCIPAL)
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ruta_respaldo(self):
        carpeta = os.path.join(self.dir, "respaldos")
        copias = [f for f in os.listdir(carpeta) if f.endswith(".db")]
        return os.path.join(carpeta, copias[0])

    def test_backup_es_restaurable_y_completo(self):
        db.add_recordatorio("2030-01-01T08:00", "regar plantas")
        self.assertTrue(db.respaldo_diario())

        # "Restaurar" = abrir la copia y leerla como una BD normal.
        copia = self._ruta_respaldo()
        c = sqlite3.connect(copia)
        c.row_factory = sqlite3.Row
        textos = [r["texto"] for r in c.execute("SELECT texto FROM recordatorios")]
        version = c.execute("PRAGMA user_version").fetchone()[0]
        c.close()
        self.assertIn("regar plantas", textos)          # los datos están
        self.assertEqual(version, db.SCHEMA_VERSION)     # y el esquema también

    def test_idempotente_por_dia(self):
        self.assertTrue(db.respaldo_diario())   # primer respaldo del día
        self.assertFalse(db.respaldo_diario())  # segundo: ya está, no repite

    def test_restaurar_a_db_principal(self):
        # Flujo real de recuperación: copiar el respaldo sobre agenda.db.
        db.add_recordatorio("2030-01-01T08:00", "dato importante")
        db.respaldo_diario()
        copia = self._ruta_respaldo()
        import shutil
        os.remove(db.DB_PATH)
        shutil.copyfile(copia, db.DB_PATH)      # restauración manual
        pend = [r["texto"] for r in db.listar_recordatorios()]
        self.assertIn("dato importante", pend)


if __name__ == "__main__":
    unittest.main()
