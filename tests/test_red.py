# -*- coding: utf-8 -*-
"""Tests del manejo de errores de red TIPADO en asistente.api_telegram.

Se mockea urllib.request.urlopen para simular cada clase de fallo y se verifica
que la funcion NUNCA lanza: siempre devuelve un dict, y reintenta solo cuando
tiene sentido (429, 5xx, red caida) y no cuando es permanente (4xx).

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import io
import os
import sys
import json
import socket
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import asistente  # noqa: E402


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        url="x", code=code, msg="err", hdrs=None, fp=io.BytesIO(body))


class _Resp:
    """Context manager que imita la respuesta de urlopen."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


class ApiTelegramRedTest(unittest.TestCase):
    def setUp(self):
        # No dormir de verdad en los reintentos.
        self._sleep = mock.patch("asistente.time.sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_ok_devuelve_json(self):
        with mock.patch("asistente.urllib.request.urlopen",
                        return_value=_Resp({"ok": True, "result": 1})):
            r = asistente.api_telegram("getMe", {}, "T")
        self.assertEqual(r, {"ok": True, "result": 1})

    def test_4xx_no_reintenta(self):
        url = mock.patch("asistente.urllib.request.urlopen",
                         side_effect=_http_error(400)).start()
        r = asistente.api_telegram("sendMessage", {}, "T", reintentos=3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "http_400")
        self.assertEqual(url.call_count, 1)  # NO reintenta un error nuestro

    def test_5xx_reintenta_y_se_rinde(self):
        url = mock.patch("asistente.urllib.request.urlopen",
                         side_effect=_http_error(503)).start()
        r = asistente.api_telegram("sendMessage", {}, "T", reintentos=3)
        self.assertFalse(r["ok"])
        self.assertEqual(url.call_count, 3)  # reintenta todo lo permitido

    def test_red_caida_reintenta_y_devuelve_dict(self):
        url = mock.patch(
            "asistente.urllib.request.urlopen",
            side_effect=urllib.error.URLError("sin red")).start()
        r = asistente.api_telegram("sendMessage", {}, "T", reintentos=2)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "red")
        self.assertEqual(url.call_count, 2)

    def test_timeout_no_lanza(self):
        mock.patch("asistente.urllib.request.urlopen",
                   side_effect=socket.timeout("lento")).start()
        r = asistente.api_telegram("sendMessage", {}, "T", reintentos=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "red")

    def test_429_respeta_retry_after_y_reintenta(self):
        # primero 429, luego OK -> debe acabar bien tras esperar.
        seq = [_http_error(429, b'{"parameters":{"retry_after":2}}'),
               _Resp({"ok": True})]

        def lado(*a, **k):
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v
        mock.patch("asistente.urllib.request.urlopen",
                   side_effect=lado).start()
        r = asistente.api_telegram("sendMessage", {}, "T", reintentos=3)
        self.assertTrue(r["ok"])
        self._sleep.assert_called()  # espero el retry_after


if __name__ == "__main__":
    unittest.main()
