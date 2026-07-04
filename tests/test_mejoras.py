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
import datetime
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
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


class LockDobleInstanciaTest(unittest.TestCase):
    def test_segunda_instancia_no_obtiene_lock(self):
        if bot.fcntl is None:
            self.skipTest("fcntl no disponible (no POSIX)")
        primero = bot.tomar_lock()
        self.assertTrue(primero)            # la 1ª instancia obtiene el lock
        try:
            segundo = bot.tomar_lock()
            self.assertIsNone(segundo)      # la 2ª en la misma máquina: rechazada
        finally:
            primero.close()                 # libera el lock


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


class SaludServiciosTest(unittest.TestCase):
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

    def test_todo_bien_sin_alertas(self):
        db.estado_set("ia_fallos_seguidos", 0)
        db.estado_set("latido", 1000.0)
        self.assertEqual(bot.salud_servicios(ahora=1000.0), [])

    def test_ia_caida_alerta(self):
        db.estado_set("ia_fallos_seguidos", bot.IA_FALLOS_ALERTA)
        db.estado_set("latido", 1000.0)
        probs = bot.salud_servicios(ahora=1000.0)
        self.assertTrue(any("IA" in p for p in probs))

    def test_sin_red_alerta(self):
        db.estado_set("ia_fallos_seguidos", 0)
        db.estado_set("latido", 1000.0)
        probs = bot.salud_servicios(ahora=1000.0 + bot.LATIDO_MAX_S + 60)
        self.assertTrue(any("Telegram" in p for p in probs))

    def test_latido_cero_no_alerta(self):
        # Sin latido previo (arranque): no inventamos una alarma de "sin red".
        db.estado_set("ia_fallos_seguidos", 0)
        db.estado_set("latido", 0)
        self.assertEqual(bot.salud_servicios(ahora=1e9), [])

    def test_salud_texto_todo_bien(self):
        db.estado_set("ia_fallos_seguidos", 0)
        db.estado_set("latido", 1000.0)
        txt = bot.salud_texto(ahora=1010.0)
        self.assertIn("Salud del bot", txt)
        self.assertIn("🟢", txt)            # latido fresco e IA bien
        self.assertNotIn("Avisos", txt)     # sin problemas

    def test_salud_texto_muestra_avisos(self):
        db.estado_set("ia_fallos_seguidos", bot.IA_FALLOS_ALERTA)
        db.estado_set("latido", 1000.0)
        txt = bot.salud_texto(ahora=1000.0 + bot.LATIDO_MAX_S + 60)
        self.assertIn("Avisos", txt)
        self.assertIn("📡", txt)            # sin contacto con Telegram
        self.assertIn("🧠", txt)            # IA en fallo


class PausarInsistenciaTest(unittest.TestCase):
    """Bug real: el usuario decía 'pausa / yo te aviso', la IA respondía 'de
    acuerdo' por charla y el recordatorio insistente seguía sonando cada X min.
    Ahora una frase de pausa lo calla de inmediato."""
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

    def _crear_insistente(self):
        return db.add_recordatorio("2030-01-01T10:00", "llevar las imágenes",
                                   insistir_min=30, insistir_veces=-1)

    def test_pausa_calla_el_insistente(self):
        self._crear_insistente()
        resp, _ = bot.procesar_simple("vamos a pausar, yo te aviso", [], estricto=True)
        self.assertIn("🔕", resp)
        # Ya no queda como pendiente: no volverá a sonar.
        self.assertEqual(db.listar_recordatorios(), [])

    def test_otras_frases_de_pausa(self):
        for frase in ("deja de recordarme eso", "silencia el recordatorio",
                      "ya no me insistas", "detente con eso"):
            self._crear_insistente()
            resp, _ = bot.procesar_simple(frase, [], estricto=True)
            self.assertIn("🔕", resp, f"falló: {frase!r}")
            self.assertEqual(db.listar_recordatorios(), [])

    def test_sin_insistente_no_actua(self):
        # Sin recordatorio insistente, una frase con 'para' no debe falsear.
        out = bot.procesar_simple("comprar pan para mañana", [], estricto=True)
        self.assertIsNone(out)  # que decida la IA, no lo capturamos


class BorronTotalTest(unittest.TestCase):
    """Borrar TODO debe ser recuperable durante 24h (papelera)."""
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

    def _sembrar(self):
        db.add_recordatorio("2030-01-01T10:00", "regar plantas")
        db.add_nota("idea para el horno")
        pid = db.add_proyecto("pizzeria", "horno de barro")
        db.add_fase(pid, "comprar ladrillos")

    def test_borra_y_recupera_todo(self):
        self._sembrar()
        total, papid = db.borrar_todo()
        self.assertGreaterEqual(total, 4)        # rec + nota + proyecto + fase
        self.assertIsNotNone(papid)
        self.assertEqual(db.listar_recordatorios(), [])
        self.assertEqual(db.buscar_notas(), [])
        self.assertEqual(db.cargar_proyectos(), [])
        # Deshacer dentro de 24h restaura todo.
        n = db.recuperar_todo(papelera_id=papid)
        self.assertEqual(n, total)
        self.assertEqual(len(db.listar_recordatorios()), 1)
        self.assertEqual(len(db.buscar_notas()), 1)
        proys = db.cargar_proyectos()
        self.assertEqual(len(proys), 1)
        self.assertEqual(len(proys[0]["fases"]), 1)   # la fase volvió a su proyecto

    def test_no_recupera_pasadas_24h(self):
        self._sembrar()
        viejo = datetime.datetime.now() - datetime.timedelta(hours=25)
        total, _ = db.borrar_todo(ahora=viejo)
        self.assertGreaterEqual(total, 1)
        self.assertIsNone(db.recuperar_todo())   # ya expiró

    def test_borrar_vacio_no_crea_papelera(self):
        self.assertEqual(db.borrar_todo(), (0, None))

    def test_aislado_por_dueno(self):
        with db.como_dueno("ana"):
            db.add_nota("nota de ana")
        with db.como_dueno("beto"):
            db.add_nota("nota de beto")
            total, _ = db.borrar_todo()
        self.assertEqual(total, 1)               # solo borró lo de beto
        with db.como_dueno("ana"):
            self.assertEqual(len(db.buscar_notas()), 1)


class ContarUsuariosTest(unittest.TestCase):
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

    def test_cuenta_principal_y_externos(self):
        with db.como_dueno(db.DUENO_PRINCIPAL):
            db.add_nota("mía")
        with db.como_dueno("111"):
            db.add_nota("de un amigo")
        with db.como_dueno("222"):
            db.add_recordatorio("2030-01-01T10:00", "x")
        u = db.contar_usuarios()
        self.assertEqual(u["total"], 3)
        self.assertTrue(u["tiene_principal"])
        self.assertEqual(u["externos"], ["111", "222"])

    def test_bd_vacia(self):
        u = db.contar_usuarios()
        self.assertEqual(u["total"], 0)
        self.assertEqual(u["externos"], [])


class NovedadesMatutinasTest(unittest.TestCase):
    """El parte matutino anexa el changelog UNA sola vez por version."""
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        db.estado_set("novedades_version", "3.1")
        db.estado_set("novedades_texto", "Parte de novedades 3.1")

    def tearDown(self):
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def test_anexa_una_sola_vez(self):
        self.assertEqual(asistente.novedades_para_resumen(db), "Parte de novedades 3.1")
        # La segunda mañana ya no repite (misma version).
        self.assertEqual(asistente.novedades_para_resumen(db), "")

    def test_nueva_version_vuelve_a_anunciar(self):
        asistente.novedades_para_resumen(db)               # ve la 3.1
        db.estado_set("novedades_version", "3.2")
        db.estado_set("novedades_texto", "Parte 3.2")
        self.assertEqual(asistente.novedades_para_resumen(db), "Parte 3.2")

    def test_sin_novedades_no_anexa(self):
        db.estado_set("novedades_version", "")
        self.assertEqual(asistente.novedades_para_resumen(db), "")


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


class NombreYTratamientoTest(unittest.TestCase):
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

    def test_sin_nombre_siempre_senor(self):
        self.assertIsNone(db.get_nombre())
        self.assertEqual(db.tratamiento(), "señor")
        self.assertEqual(db.tratamiento(), "señor")

    def test_alterna_nombre_y_senor(self):
        db.set_nombre("Samuel")
        self.assertEqual(db.get_nombre(), "Samuel")
        self.assertEqual(db.tratamiento(), "señor Samuel")
        self.assertEqual(db.tratamiento(), "señor")
        self.assertEqual(db.tratamiento(), "señor Samuel")

    def test_titulo_por_defecto_y_valido(self):
        self.assertEqual(db.get_titulo(), "señor")
        self.assertEqual(db.set_titulo("sra"), "señora")
        self.assertEqual(db.get_titulo(), "señora")
        self.assertEqual(db.set_titulo("señorita"), "señorita")
        self.assertEqual(db.get_titulo(), "señorita")
        # Valor invalido -> cae a 'señor'.
        self.assertEqual(db.set_titulo("cualquiera"), "señor")

    def test_tratamiento_usa_el_titulo_elegido(self):
        db.set_nombre("Ana")
        db.set_titulo("srta")
        self.assertEqual(db.tratamiento(), "señorita Ana")
        self.assertEqual(db.tratamiento(), "señorita")
        self.assertEqual(db.tratamiento(), "señorita Ana")

    def test_titulo_sin_nombre_es_solo_el_titulo(self):
        db.set_titulo("señora")
        self.assertIsNone(db.get_nombre())
        self.assertEqual(db.tratamiento(), "señora")

    def test_avanzar_false_no_mueve_el_turno(self):
        db.set_nombre("Samuel")
        self.assertEqual(db.tratamiento(avanzar=False), "señor Samuel")
        self.assertEqual(db.tratamiento(avanzar=False), "señor Samuel")

    def test_nombre_aislado_por_dueno(self):
        db.set_nombre("Samuel", dueno=db.DUENO_PRINCIPAL)
        self.assertIsNone(db.get_nombre(dueno="otro"))
        self.assertEqual(db.tratamiento(dueno="otro"), "señor")

    def test_extraer_nombre(self):
        self.assertEqual(bot._extraer_nombre("Samuel"), "Samuel")
        self.assertEqual(bot._extraer_nombre("me llamo Samuel"), "Samuel")
        self.assertEqual(bot._extraer_nombre("soy Samuel."), "Samuel")
        self.assertEqual(bot._extraer_nombre("mi nombre es Samuel"), "Samuel")
        self.assertIsNone(bot._extraer_nombre(""))
        self.assertIsNone(bot._extraer_nombre("/menu"))
        self.assertIsNone(bot._extraer_nombre("x" * 41))

    def test_onboarding_pregunta_y_guarda(self):
        enviados = []
        orig_enviar = bot.A.enviar_mensaje
        bot.A.enviar_mensaje = lambda txt, *a, **k: enviados.append(txt)
        orig_desde = bot.ONBOARDING_DESDE
        bot.ONBOARDING_DESDE = datetime.date(2000, 1, 1)
        try:
            cid = "555"
            # 1er mensaje: se presenta y pregunta el nombre.
            self.assertTrue(bot._onboarding("hola", {}, "tok", cid))
            self.assertIn("nombre", enviados[-1].lower())
            self.assertIsNone(db.get_nombre())
            # 2do mensaje: lo captura y lo guarda.
            self.assertTrue(bot._onboarding("Samuel", {}, "tok", cid))
            self.assertEqual(db.get_nombre(), "Samuel")
            # 3er mensaje: ya no intercepta (sigue el flujo normal).
            self.assertFalse(bot._onboarding("lista", {}, "tok", cid))
        finally:
            bot.A.enviar_mensaje = orig_enviar
            bot.ONBOARDING_DESDE = orig_desde

    def test_onboarding_no_arranca_antes_de_la_fecha(self):
        orig_desde = bot.ONBOARDING_DESDE
        bot.ONBOARDING_DESDE = datetime.date(2999, 1, 1)
        try:
            self.assertFalse(bot._onboarding("hola", {}, "tok", "999"))
        finally:
            bot.ONBOARDING_DESDE = orig_desde


if __name__ == "__main__":
    unittest.main()
