"""Tests de las notas de voz: descarga, transcripcion y enrutado (todo mock)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asistente as A  # noqa: E402
import bot  # noqa: E402
import gemini_ia  # noqa: E402


class TranscribirTest(unittest.TestCase):
    def test_sin_clave_devuelve_none(self):
        self.assertIsNone(gemini_ia.transcribir(b"x", api_key=""))

    def test_sin_audio_devuelve_none(self):
        self.assertIsNone(gemini_ia.transcribir(b"", api_key="k"))

    def test_inaudible_se_traduce_a_none(self):
        orig = gemini_ia._llamar
        gemini_ia._llamar = lambda cuerpo, key: "(inaudible)"
        try:
            self.assertIsNone(gemini_ia.transcribir(b"abc", api_key="k"))
        finally:
            gemini_ia._llamar = orig

    def test_devuelve_texto_limpio(self):
        capturado = {}

        def fake(cuerpo, key):
            capturado["cuerpo"] = cuerpo
            return "  compra pan  "

        orig = gemini_ia._llamar
        gemini_ia._llamar = fake
        try:
            self.assertEqual(gemini_ia.transcribir(b"abc", api_key="k"),
                             "compra pan")
        finally:
            gemini_ia._llamar = orig
        # El audio viaja como inline_data base64.
        partes = capturado["cuerpo"]["contents"][0]["parts"]
        self.assertTrue(any("inline_data" in p for p in partes))

    def test_no_lanza_si_llamar_revienta(self):
        orig = gemini_ia._llamar

        def boom(cuerpo, key):
            raise RuntimeError("red caida")

        gemini_ia._llamar = boom
        try:
            self.assertIsNone(gemini_ia.transcribir(b"abc", api_key="k"))
        finally:
            gemini_ia._llamar = orig


class DescargarArchivoTest(unittest.TestCase):
    def test_getfile_fallido_devuelve_none(self):
        orig = A.api_telegram
        A.api_telegram = lambda *a, **k: {"ok": False}
        try:
            self.assertIsNone(A.descargar_archivo("fid", "tok"))
        finally:
            A.api_telegram = orig

    def test_audio_grande_se_rechaza(self):
        orig = A.api_telegram
        A.api_telegram = lambda *a, **k: {
            "ok": True,
            "result": {"file_path": "voice/x.ogg", "file_size": 9_000_000},
        }
        try:
            self.assertIsNone(A.descargar_archivo("fid", "tok",
                                                  max_bytes=1_000_000))
        finally:
            A.api_telegram = orig


class ManejarVozTest(unittest.TestCase):
    def setUp(self):
        self.enviados = []
        self._env = A.enviar_mensaje
        bot.A.enviar_mensaje = lambda texto, *a, **k: self.enviados.append(texto)

    def tearDown(self):
        bot.A.enviar_mensaje = self._env

    def test_sin_clave_avisa_con_cortesia(self):
        cfg = {"gemini_api_key": ""}
        bot.manejar_voz({"voice": {"file_id": "x"}}, cfg, "tok", "1")
        self.assertTrue(self.enviados)
        self.assertIn("nota", self.enviados[0].lower() + " ")

    def test_transcribe_y_enruta(self):
        cfg = {"gemini_api_key": "k"}
        ruteado = {}
        descargar = bot.A.descargar_archivo
        transcribir = bot.gemini_ia.transcribir if bot.gemini_ia else None
        manejar = bot.manejar_mensaje
        bot.A.descargar_archivo = lambda *a, **k: b"audio-bytes"
        bot.gemini_ia.transcribir = lambda audio, mime, api_key: "compra pan"
        bot.manejar_mensaje = lambda texto, *a, **k: ruteado.setdefault("t", texto)
        try:
            bot.manejar_voz({"voice": {"file_id": "x"}}, cfg, "tok", "1")
        finally:
            bot.A.descargar_archivo = descargar
            if transcribir is not None:
                bot.gemini_ia.transcribir = transcribir
            bot.manejar_mensaje = manejar
        self.assertEqual(ruteado.get("t"), "compra pan")
        # Hizo eco de lo entendido.
        self.assertTrue(any("compra pan" in e for e in self.enviados))

    def test_transcripcion_vacia_avisa(self):
        cfg = {"gemini_api_key": "k"}
        descargar = bot.A.descargar_archivo
        transcribir = bot.gemini_ia.transcribir if bot.gemini_ia else None
        bot.A.descargar_archivo = lambda *a, **k: b"audio-bytes"
        bot.gemini_ia.transcribir = lambda audio, mime, api_key: None
        try:
            bot.manejar_voz({"voice": {"file_id": "x"}}, cfg, "tok", "1")
        finally:
            bot.A.descargar_archivo = descargar
            if transcribir is not None:
                bot.gemini_ia.transcribir = transcribir
        self.assertTrue(any("entender" in e.lower() for e in self.enviados))


if __name__ == "__main__":
    unittest.main()
