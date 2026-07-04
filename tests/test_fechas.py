# -*- coding: utf-8 -*-
"""Tests del parser de fechas (fechas.py). Sin dependencias: usa unittest.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
import datetime

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fechas  # noqa: E402

# 'Ahora' fijo para que los tests sean deterministas: jueves 11/06/2026, 09:00.
AHORA = datetime.datetime(2026, 6, 11, 9, 0)


class TestParsearRecordatorio(unittest.TestCase):
    def test_manana_a_las_diez(self):
        cuando, rep, txt = fechas.parsear(
            "recuerdame llamar al banco mañana a las 10am", AHORA)
        self.assertEqual(cuando, "2026-06-12T10:00")
        self.assertIsNone(rep)
        self.assertIn("llamar al banco", txt)

    def test_en_dos_horas(self):
        cuando, rep, txt = fechas.parsear("avisame en 2 horas sacar el pan", AHORA)
        self.assertEqual(cuando, "2026-06-11T11:00")
        self.assertIsNone(rep)
        self.assertIn("sacar el pan", txt)

    def test_repeticion_diaria(self):
        cuando, rep, txt = fechas.parsear(
            "recuerdame todos los dias a las 7am tomar la pastilla", AHORA)
        self.assertEqual(rep, "diario")
        self.assertTrue(cuando.endswith("T07:00"))
        self.assertIn("tomar la pastilla", txt)

    def test_dia_de_la_semana(self):
        # jueves -> "el viernes" es el dia siguiente
        cuando, rep, txt = fechas.parsear(
            "recuerdame el viernes a las 7pm cobrarle a Luis", AHORA)
        self.assertEqual(cuando, "2026-06-12T19:00")
        self.assertIn("cobrarle a Luis", txt)

    def test_sin_hora_clara_devuelve_none(self):
        # "a las 5" sin am/pm es ambiguo -> que decida la IA
        self.assertIsNone(fechas.parsear("recuerdame algo a las 5", AHORA))

    def test_no_es_recordatorio(self):
        self.assertIsNone(fechas.parsear("hola que tal", AHORA))

    def test_fecha_imposible_no_crashea(self):
        # 2026-13-40 no existe: el parser no debe lanzar excepcion
        try:
            res = fechas.parsear("recuerdame el 2026-13-40 a las 10am algo", AHORA)
        except Exception as e:  # noqa: BLE001
            self.fail(f"parsear lanzo excepcion con fecha imposible: {e}")
        self.assertIsNotNone(res)  # cae a 'hoy/manana' con la hora dada


class TestParsearEvento(unittest.TestCase):
    def test_agendame_con_fecha_y_hora(self):
        fecha, hora, titulo = fechas.parsear_evento(
            "agendame dentista mañana 10am", AHORA)
        self.assertEqual(fecha, "2026-06-12")
        self.assertEqual(hora, "10:00")
        self.assertEqual(titulo.lower(), "dentista")

    def test_evento_sin_fecha_devuelve_none(self):
        self.assertIsNone(fechas.parsear_evento("agendame dentista", AHORA))

    def test_no_es_evento(self):
        self.assertIsNone(fechas.parsear_evento("comprar pan", AHORA))


if __name__ == "__main__":
    unittest.main()
