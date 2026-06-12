# -*- coding: utf-8 -*-
"""Tests de las mejoras de robustez/seguridad:
- db.como_dueno: fija el dueno de forma acotada y lo restaura (incluso ante error).
- bot.permitido: rate-limit por usuario (ventana deslizante).
- asistente.cargar_config: las variables de entorno pisan config.json.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import json
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db          # noqa: E402
import bot         # noqa: E402
import asistente   # noqa: E402


class ComoDuenoTest(unittest.TestCase):
    def test_fija_y_restaura(self):
        db.set_dueno(db.DUENO_PRINCIPAL)
        with db.como_dueno("userA"):
            self.assertEqual(db._d(), "userA")
            with db.como_dueno("userB"):
                self.assertEqual(db._d(), "userB")
            self.assertEqual(db._d(), "userA")  # restaura el anidado
        self.assertEqual(db._d(), db.DUENO_PRINCIPAL)  # restaura al salir

    def test_restaura_ante_excepcion(self):
        db.set_dueno(db.DUENO_PRINCIPAL)
        with self.assertRaises(ValueError):
            with db.como_dueno("userX"):
                raise ValueError("boom")
        self.assertEqual(db._d(), db.DUENO_PRINCIPAL)


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        # Aisla el estado del rate-limiter entre tests.
        bot._rl_marcas.clear()
        bot._rl_avisado.clear()

    def test_bloquea_tras_el_maximo(self):
        emisor = "rl-test-1"
        oks = [bot.permitido(emisor)[0] for _ in range(bot.RL_MAXIMO + 5)]
        self.assertTrue(all(oks[: bot.RL_MAXIMO]))          # los primeros pasan
        self.assertFalse(any(oks[bot.RL_MAXIMO:]))          # el resto se bloquea

    def test_avisa_una_sola_vez_por_ventana(self):
        emisor = "rl-test-2"
        for _ in range(bot.RL_MAXIMO):
            bot.permitido(emisor)
        primero = bot.permitido(emisor)   # primer bloqueo
        segundo = bot.permitido(emisor)   # siguiente bloqueo en la misma ventana
        self.assertEqual(primero, (False, True))
        self.assertEqual(segundo, (False, False))

    def test_usuarios_independientes(self):
        for _ in range(bot.RL_MAXIMO):
            bot.permitido("ocupa-cuota")
        self.assertTrue(bot.permitido("otro-usuario")[0])  # no le afecta el ajeno


class ConfigEnvTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"token": "del_archivo", "gemini_api_key": "g_archivo"}, f)
        self._orig = asistente.CONFIG_PATH
        asistente.CONFIG_PATH = self.ruta

    def tearDown(self):
        asistente.CONFIG_PATH = self._orig
        os.remove(self.ruta)
        for k in ("AGENDA_TOKEN", "AGENDA_GEMINI_API_KEY"):
            os.environ.pop(k, None)

    def test_archivo_sin_env(self):
        os.environ.pop("AGENDA_TOKEN", None)
        cfg, token, _ = asistente.cargar_config()
        self.assertEqual(token, "del_archivo")
        self.assertEqual(cfg["gemini_api_key"], "g_archivo")

    def test_env_pisa_archivo(self):
        os.environ["AGENDA_TOKEN"] = "del_entorno"
        os.environ["AGENDA_GEMINI_API_KEY"] = "g_entorno"
        cfg, token, _ = asistente.cargar_config()
        self.assertEqual(token, "del_entorno")
        self.assertEqual(cfg["gemini_api_key"], "g_entorno")

    def test_permisos_600(self):
        asistente.cargar_config()
        modo = oct(os.stat(self.ruta).st_mode)[-3:]
        self.assertEqual(modo, "600")


if __name__ == "__main__":
    unittest.main()
