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


class ValidarConfigTest(unittest.TestCase):
    def test_config_buena_sin_avisos(self):
        cfg = {"token": "123456:ABCdef", "chat_id": "8717805844",
               "chat_ids": ["8717805844", "1560483859"],
               "silencio_inicio": 23, "silencio_fin": 7}
        self.assertEqual(asistente.validar_config(cfg), [])

    def test_token_invalido_avisa(self):
        avisos = asistente.validar_config({"token": "sin_dos_puntos"})
        self.assertTrue(any("token" in a.lower() for a in avisos))

    def test_chat_id_no_numerico_avisa(self):
        avisos = asistente.validar_config(
            {"token": "1:A", "chat_id": "hola"})
        self.assertTrue(any("chat_id" in a for a in avisos))

    def test_silencio_fuera_de_rango_avisa(self):
        avisos = asistente.validar_config(
            {"token": "1:A", "silencio_inicio": 99})
        self.assertTrue(any("silencio_inicio" in a for a in avisos))

    def test_chat_ids_no_lista_avisa(self):
        avisos = asistente.validar_config(
            {"token": "1:A", "chat_ids": "8717805844"})
        self.assertTrue(any("chat_ids" in a for a in avisos))


class InsistenciaAcotadaTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        db.set_dueno(db.DUENO_PRINCIPAL)

    def tearDown(self):
        db.set_dueno(db.DUENO_PRINCIPAL)
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def _forzar_vencido(self, rid):
        with db.conn() as c:
            c.execute("UPDATE recordatorios SET cuando=? WHERE id=?",
                      ("2000-01-01T08:00", rid))

    def _fila(self, rid):
        with db.conn() as c:
            return dict(c.execute("SELECT * FROM recordatorios WHERE id=?",
                                  (rid,)).fetchone())

    def test_sin_configurar_avisa_una_sola_vez(self):
        rid = db.add_recordatorio("2000-01-01T08:00", "entregar algo")
        db.marcar_enviado(db.recordatorios_vencidos()[0])
        self.assertEqual(self._fila(rid)["enviado"], 1)
        self.assertEqual(db.recordatorios_vencidos(), [])

    def test_insiste_solo_las_veces_pedidas(self):
        rid = db.add_recordatorio("2000-01-01T08:00", "pagar luz")
        self.assertTrue(db.configurar_insistencia(rid, 30, 2))
        # Mientras le queden insistencias: re-agenda, descuenta y NO se apaga.
        for esperado in (2, 1):
            self._forzar_vencido(rid)
            r = db.recordatorios_vencidos()[0]
            self.assertEqual(r["insistir_veces"], esperado)
            self.assertEqual(r["enviado"], 0)
            db.marcar_enviado(r)
        # Agotadas las 2: el siguiente envío lo apaga.
        self._forzar_vencido(rid)
        r = db.recordatorios_vencidos()[0]
        self.assertEqual(r["insistir_veces"], 0)
        db.marcar_enviado(r)
        self.assertEqual(self._fila(rid)["enviado"], 1)


class PosponerMadrugadaTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        db.set_dueno(db.DUENO_PRINCIPAL)

    def tearDown(self):
        db.set_dueno(db.DUENO_PRINCIPAL)
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def _cuando(self, rid):
        with db.conn() as c:
            return c.execute("SELECT cuando FROM recordatorios WHERE id=?",
                             (rid,)).fetchone()[0]

    def test_difiere_la_no_pedida_a_la_manana(self):
        import datetime as dt
        rid = db.add_recordatorio("2030-01-01T02:00", "algo de noche")
        ahora = dt.datetime(2030, 1, 1, 2, 5)
        movidos = db.posponer_madrugada((23, 7), ahora=ahora)
        self.assertEqual(movidos, 1)
        self.assertEqual(self._cuando(rid), "2030-01-01T07:00")

    def test_respeta_hora_explicita(self):
        import datetime as dt
        rid = db.add_recordatorio("2030-01-01T02:00", "alarma a propósito",
                                  hora_explicita=True)
        ahora = dt.datetime(2030, 1, 1, 2, 5)
        self.assertEqual(db.posponer_madrugada((23, 7), ahora=ahora), 0)
        self.assertEqual(self._cuando(rid), "2030-01-01T02:00")

    def test_no_toca_las_diurnas(self):
        import datetime as dt
        rid = db.add_recordatorio("2030-01-01T15:00", "de día")
        ahora = dt.datetime(2030, 1, 1, 15, 5)
        self.assertEqual(db.posponer_madrugada((23, 7), ahora=ahora), 0)
        self.assertEqual(self._cuando(rid), "2030-01-01T15:00")


class SchemaVersionTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta

    def tearDown(self):
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def test_db_fresca_queda_en_la_version_actual(self):
        db.init_db()
        self.assertEqual(db.schema_version(), db.SCHEMA_VERSION)

    def test_init_db_es_idempotente(self):
        db.init_db()
        db.init_db()  # segundo arranque: no debe fallar ni cambiar la version
        self.assertEqual(db.schema_version(), db.SCHEMA_VERSION)
        # y las columnas migradas existen
        with db.conn() as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(recordatorios)")]
        for col in ("insistir_min", "grupo", "insistir_veces", "dueno",
                    "hora_explicita"):
            self.assertIn(col, cols)


class MetricasTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def test_inc_acumula(self):
        db.metrica_inc("mensajes")
        db.metrica_inc("mensajes", 4)
        self.assertEqual(db.metricas()["mensajes"], 5)

    def test_observar_calcula_promedio(self):
        db.metrica_observar("ia", 100)
        db.metrica_observar("ia", 300)
        m = db.metricas()
        self.assertEqual(m["ia_n"], 2)
        self.assertEqual(m["ia_ms_prom"], 200)  # (100+300)/2

    def test_metricas_vacias(self):
        self.assertEqual(db.metricas(), {})

    def test_uso_inc_acumula(self):
        for _ in range(3):
            db.uso_inc()
        hoy = db.uso_resumen(1)[0]
        self.assertEqual(hoy[1], 3)


class SilencioNocturnoTest(unittest.TestCase):
    def test_saca_de_madrugada(self):
        import datetime as dt
        # 02:00 cae en la franja [23,7): se mueve a las 07:00 del mismo día.
        m = dt.datetime(2030, 1, 1, 2, 0)
        s = db._sacar_de_silencio(m, (23, 7))
        self.assertEqual((s.hour, s.minute), (7, 0))
        self.assertEqual(s.date(), m.date())

    def test_respeta_horario_diurno(self):
        import datetime as dt
        # 15:00 NO está en silencio: se deja igual.
        m = dt.datetime(2030, 1, 1, 15, 0)
        self.assertEqual(db._sacar_de_silencio(m, (23, 7)), m)

    def test_tarde_noche_pasa_al_dia_siguiente(self):
        import datetime as dt
        # 23:30 está en silencio; el fin (07:00) es del día siguiente.
        m = dt.datetime(2030, 1, 1, 23, 30)
        s = db._sacar_de_silencio(m, (23, 7))
        self.assertEqual((s.hour, s.minute), (7, 0))
        self.assertEqual(s.date(), dt.date(2030, 1, 2))


if __name__ == "__main__":
    unittest.main()
