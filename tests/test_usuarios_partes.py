"""Tests del registro de usuarios y del opt-in al parte de buenos dias/noches."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asistente as A  # noqa: E402
import bot  # noqa: E402
import db  # noqa: E402


class _DBTemporal(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        self.enviados = []
        self._env = bot.A.enviar_mensaje
        bot.A.enviar_mensaje = lambda texto, *a, **k: self.enviados.append(
            (texto, k.get("botones")))

    def tearDown(self):
        bot.A.enviar_mensaje = self._env
        db.DB_PATH = self._orig
        os.unlink(self.ruta)


class RegistroUsuariosTest(_DBTemporal):
    def test_registrar_visto_y_listar(self):
        self.assertEqual(db.usuarios_registrados(), [])
        db.registrar_visto("123")
        db.registrar_visto("456")
        db.registrar_visto("123")  # idempotente
        cids = {u["chat_id"] for u in db.usuarios_registrados()}
        self.assertEqual(cids, {"123", "456"})

    def test_partes_por_defecto_none(self):
        db.registrar_visto("123")
        self.assertIsNone(db.get_partes("123"))

    def test_set_partes_si_no(self):
        db.set_partes("123", True)
        db.set_partes("456", False)
        self.assertEqual(db.get_partes("123"), 1)
        self.assertEqual(db.get_partes("456"), 0)
        self.assertEqual(db.usuarios_con_partes(), ["123"])


class OfrecerPartesTest(_DBTemporal):
    def test_ofrece_una_sola_vez_a_externo(self):
        cfg = {"chat_ids": ["999"]}  # 999 es el dueño; 123 es externo
        bot._ofrecer_partes(cfg, "tok", "123")
        self.assertEqual(len(self.enviados), 1)
        self.assertIsNotNone(self.enviados[0][1])  # lleva botones
        # No vuelve a preguntar.
        bot._ofrecer_partes(cfg, "tok", "123")
        self.assertEqual(len(self.enviados), 1)

    def test_no_ofrece_al_dueno(self):
        cfg = {"chat_ids": ["999"]}
        bot._ofrecer_partes(cfg, "tok", "999")
        self.assertEqual(self.enviados, [])

    def test_no_ofrece_si_ya_respondio(self):
        cfg = {"chat_ids": ["999"]}
        db.set_partes("123", True)
        bot._ofrecer_partes(cfg, "tok", "123")
        self.assertEqual(self.enviados, [])


class CallbackPartesTest(_DBTemporal):
    def _cb(self, data):
        return {"id": "x", "data": data,
                "message": {"chat": {"id": "123"}}}

    def test_boton_si_activa(self):
        cfg = {"chat_ids": ["999"]}
        bot.manejar_boton(self._cb("partes:si"), cfg, "tok", "123")
        self.assertEqual(db.get_partes("123"), 1)

    def test_boton_no_desactiva(self):
        cfg = {"chat_ids": ["999"]}
        bot.manejar_boton(self._cb("partes:no"), cfg, "tok", "123")
        self.assertEqual(db.get_partes("123"), 0)


class OptInDestinatariosTest(_DBTemporal):
    def test_chats_opt_in_excluye_dueno(self):
        cfg = {"chat_ids": ["999"]}
        db.set_partes("999", True)   # dueño: ya lo recibe por config
        db.set_partes("123", True)   # externo opt-in
        db.set_partes("456", False)  # externo que dijo no
        self.assertEqual(A.chats_opt_in(cfg, db), ["123"])


if __name__ == "__main__":
    unittest.main()
