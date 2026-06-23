"""Tests del versiculo (loader stdlib) y de la tarjeta PNG (estilo D)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import db  # noqa: E402
import tarjeta  # noqa: E402
import versiculos  # noqa: E402


class VersiculosTest(unittest.TestCase):
    def test_parsear_bloques(self):
        texto = (
            "# comentario que se ignora\n"
            'Juan 3:16 — "De tal manera amó Dios..."\n'
            "Comentario de Larry, señor.\n"
            "\n"
            'Salmos 23:1 — "Jehová es mi pastor."\n'
            "Nada me faltará, señor.\n"
        )
        pares = versiculos._parsear(texto)
        self.assertEqual(len(pares), 2)
        self.assertIn("Juan 3:16", pares[0][0])
        self.assertIn("Larry", pares[0][1])

    def test_carpeta_real_tiene_versiculos(self):
        self.assertGreaterEqual(len(versiculos.cargar()), 50)

    def test_formato_html_escapa(self):
        html = versiculos.formato_html(("A & B", "<malicia>"))
        self.assertIn("&amp;", html)
        self.assertIn("&lt;malicia&gt;", html)
        self.assertIn("<b>", html)

    def test_formato_html_none(self):
        self.assertEqual(versiculos.formato_html(None), "")

    def test_aleatorio_de_carpeta_vacia(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(versiculos.aleatorio(d))
            self.assertEqual(versiculos.para_parte(d), "")


class TarjetaTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig
        os.unlink(self.ruta)

    def test_genera_png_valido(self):
        png = tarjeta.generar()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(png), 500)
        # IEND al final (chunk: longitud 0 + 'IEND' + CRC).
        self.assertEqual(png[-8:], b"IEND\xaeB`\x82")

    def test_texto_no_revienta_con_acentos_y_ñ(self):
        lz = tarjeta._Lienzo(100, 20, tarjeta._CREMA)
        tarjeta._texto(lz, "AÑO ÉXITO", 0, 0, tarjeta._TINTA, 2)
        self.assertEqual(len(lz.bytes()[:8]), 8)


class CapturaProgresoTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        self.enviados = []
        self._orig_enviar = bot.A.enviar_mensaje
        bot.A.enviar_mensaje = lambda *a, **k: self.enviados.append(a)

    def tearDown(self):
        bot.A.enviar_mensaje = self._orig_enviar
        db.DB_PATH = self._orig
        os.unlink(self.ruta)

    def test_sin_flag_no_intercepta(self):
        self.assertFalse(bot._capturar_progreso("hola", "T", 1))

    def test_guarda_avance(self):
        db.estado_set(bot._clave_esperando_progreso(1), "1")
        corto = bot._capturar_progreso("Ordené el garaje", "T", 1)
        self.assertTrue(corto)
        acts = db.actividad_de()
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["texto"], "Ordené el garaje")
        # El flag queda desactivado tras capturar.
        self.assertFalse(bot._capturar_progreso("otra cosa", "T", 1))

    def test_cancelar_no_guarda(self):
        db.estado_set(bot._clave_esperando_progreso(1), "1")
        self.assertTrue(bot._capturar_progreso("nada", "T", 1))
        self.assertEqual(db.actividad_de(), [])


if __name__ == "__main__":
    unittest.main()
