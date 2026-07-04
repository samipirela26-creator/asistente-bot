# -*- coding: utf-8 -*-
"""Tests end-to-end de los handlers del bot (manejar_mensaje / manejar_boton)
con la API de Telegram MOCKEADA: no se toca la red.

La idea es ejercitar el "corazon" del bot (el ruteo de un mensaje y de un
boton) sin necesidad de token ni de IA. Para eso:
- se usa una BD temporal aislada,
- se reemplaza bot.A.enviar_mensaje y bot.A.api_telegram por espias que
  guardan lo que se "habria enviado",
- se usa un cfg SIN gemini_api_key, de modo que el flujo cae en el parser por
  reglas (procesar_simple), 100% local y determinista.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import db    # noqa: E402
import bot   # noqa: E402


class HandlerBase(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig_db = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        db.set_dueno(db.DUENO_PRINCIPAL)
        # Usuario ya presentado: evita que el onboarding (preguntar el nombre)
        # intercepte el primer mensaje de estos tests de ruteo.
        db.set_nombre("Samuel")

        # Espia de envios: cada llamada guarda (texto, chat_id, botones).
        self.enviados = []
        self._orig_enviar = bot.A.enviar_mensaje
        self._orig_api = bot.A.api_telegram
        bot.A.enviar_mensaje = lambda texto, token, chat_id, botones=None: (
            self.enviados.append((texto, chat_id, botones)) or {"ok": True})
        # api_telegram (p.ej. answerCallbackQuery) no debe tocar la red.
        self.api_llamadas = []
        bot.A.api_telegram = lambda metodo, params, token, *a, **k: (
            self.api_llamadas.append((metodo, params)) or {"ok": True})

        self.cfg = {"token": "T", "chat_id": "123"}  # sin gemini_api_key

    def tearDown(self):
        bot.A.enviar_mensaje = self._orig_enviar
        bot.A.api_telegram = self._orig_api
        db.set_dueno(db.DUENO_PRINCIPAL)
        db.DB_PATH = self._orig_db
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    # --- utilidades de asercion ---
    @property
    def textos(self):
        return [t for (t, _cid, _b) in self.enviados]

    def ultimo(self):
        return self.enviados[-1] if self.enviados else (None, None, None)


class MensajeTest(HandlerBase):
    def test_menu_envia_botones(self):
        bot.manejar_mensaje("menu", self.cfg, "T", "123")
        _texto, _cid, botones = self.ultimo()
        self.assertIsNotNone(botones)          # el menu lleva teclado inline
        self.assertTrue(any("Qué hacemos" in t or "hacemos" in t
                            for t in self.textos))

    def test_lista_vacia_responde_algo(self):
        bot.manejar_mensaje("lista", self.cfg, "T", "123")
        self.assertTrue(self.enviados)         # responde sin reventar

    def test_prefijo_se_antepone(self):
        bot.manejar_mensaje("lista", self.cfg, "T", "123", prefijo="PREF ")
        self.assertTrue(any(t.startswith("PREF ") for t in self.textos))

    def test_no_toca_la_red(self):
        # Garantia central: ningun handler debe llamar a la API real.
        bot.manejar_mensaje("menu", self.cfg, "T", "123")
        bot.manejar_mensaje("lista", self.cfg, "T", "123")
        # api_telegram solo se permitiria mockeada; aqui ni siquiera se usa.
        self.assertTrue(all(m == "answerCallbackQuery"
                            for (m, _p) in self.api_llamadas))


class BotonTest(HandlerBase):
    def test_menu_lista_rutea_a_mensaje(self):
        bot.manejar_boton({"id": "1", "data": "menu:lista"},
                          self.cfg, "T", "123")
        self.assertTrue(self.enviados)
        # confirma a Telegram que atendio el boton
        self.assertIn("answerCallbackQuery",
                      [m for (m, _p) in self.api_llamadas])

    def test_ins_set_configura_y_avisa(self):
        rid = db.add_recordatorio("2000-01-01T08:00", "pagar luz")
        bot.manejar_boton({"id": "1", "data": f"ins_set:{rid}:30:3"},
                          self.cfg, "T", "123")
        self.assertTrue(any("insistir" in t.lower() for t in self.textos))
        with db.conn() as c:
            fila = dict(c.execute(
                "SELECT * FROM recordatorios WHERE id=?", (rid,)).fetchone())
        self.assertEqual(fila["insistir_veces"], 3)
        self.assertEqual(fila["insistir_min"], 30)

    def test_ins_set_super_insistente(self):
        rid = db.add_recordatorio("2000-01-01T08:00", "entregar algo")
        bot.manejar_boton({"id": "1", "data": f"ins_set:{rid}:30:-1"},
                          self.cfg, "T", "123")
        self.assertTrue(any("súper" in t.lower() or "super" in t.lower()
                            for t in self.textos))
        with db.conn() as c:
            fila = dict(c.execute(
                "SELECT * FROM recordatorios WHERE id=?", (rid,)).fetchone())
        self.assertEqual(fila["insistir_veces"], -1)

    def test_boton_invalido_no_revienta(self):
        # data corrupta: el handler debe avisar con elegancia, no crashear.
        bot.manejar_boton({"id": "1", "data": "basura-sin-dos-puntos"},
                          self.cfg, "T", "123")
        # se atendio el callback (answerCallbackQuery) igual
        self.assertIn("answerCallbackQuery",
                      [m for (m, _p) in self.api_llamadas])


if __name__ == "__main__":
    unittest.main()
