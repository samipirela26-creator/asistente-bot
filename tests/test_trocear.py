# -*- coding: utf-8 -*-
"""Tests de asistente._trocear: parte mensajes largos para Telegram (tope 4096).

Es pura y crítica: si un trozo supera el límite, Telegram rechaza el envío y el
usuario no recibe el mensaje. Las invariantes que verificamos:
  - ningún trozo excede el límite,
  - el contenido se conserva (nada se pierde),
  - no corta a media línea salvo que una sola línea pase del límite.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import asistente as A  # noqa: E402


class TrocearTest(unittest.TestCase):
    def test_texto_corto_un_solo_trozo(self):
        self.assertEqual(A._trocear("hola", 100), ["hola"])

    def test_ningun_trozo_supera_el_limite(self):
        texto = "\n".join(f"linea numero {i} con algo de relleno" for i in range(200))
        for trozo in A._trocear(texto, 100):
            self.assertLessEqual(len(trozo), 100)

    def test_linea_mas_larga_que_el_limite_se_corta_duro(self):
        texto = "x" * 50
        trozos = A._trocear(texto, 20)
        self.assertTrue(all(len(t) <= 20 for t in trozos))
        self.assertEqual("".join(trozos), texto)  # nada se pierde

    def test_no_corta_a_media_linea_si_cabe(self):
        # tres líneas que no caben juntas pero sí separadas: no se parten.
        texto = "aaa\nbbb\nccc"
        trozos = A._trocear(texto, 5)
        for t in trozos:
            for linea in t.split("\n"):
                self.assertIn(linea, ("aaa", "bbb", "ccc"))

    def test_conserva_todas_las_lineas(self):
        lineas = [f"item {i}" for i in range(50)]
        texto = "\n".join(lineas)
        trozos = A._trocear(texto, 40)
        rejuntado = "\n".join(trozos).split("\n")
        self.assertEqual(rejuntado, lineas)


if __name__ == "__main__":
    unittest.main()
