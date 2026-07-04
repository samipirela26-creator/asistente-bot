# -*- coding: utf-8 -*-
"""Test de INTEGRACIÓN del camino crítico de recordatorios, sin red.

Ejercita la cadena completa tal como la usa el hilo 'vigilar_recordatorios':
  crear -> posponer_madrugada (difiere lo no-pedido de noche)
        -> recordatorios_vencidos (a la hora correcta)
        -> destinos_de (cada aviso a SU dueño, aislado)
        -> marcar_enviado (insistencia que evita la madrugada).

Esta es justo la regresión que se nos coló (aviso de madrugada que nadie
pidió): un test de integración debe cazarla, no el usuario a la 1am.

Ejecutar desde la carpeta asistente/:
    python3 -m unittest discover -s tests
"""

import os
import sys
import datetime as dt
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import db    # noqa: E402
import bot   # noqa: E402

SILENCIO = (23, 7)


class CaminoCriticoTest(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig = db.DB_PATH
        db.DB_PATH = self.ruta
        db.init_db()
        db.set_dueno(db.DUENO_PRINCIPAL)
        # cfg: el dueño principal tiene DOS chats; '999' es otro usuario.
        self.cfg = {"token": "T", "chat_ids": ["123", "456"]}

    def tearDown(self):
        db.set_dueno(db.DUENO_PRINCIPAL)
        db.DB_PATH = self._orig
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta + suf)
            except OSError:
                pass

    def _repartir(self, ahora):
        """Replica el núcleo del bucle de vigilar_recordatorios: difiere la
        madrugada, toma los vencidos y los reparte a sus destinos. Devuelve la
        lista de (texto, chat_id) que se 'habrían enviado'."""
        db.posponer_madrugada(SILENCIO, ahora=ahora)
        enviados = []
        for r in db.recordatorios_vencidos(ahora=ahora):
            for cid in bot.destinos_de(r.get("dueno") or db.DUENO_PRINCIPAL, self.cfg):
                enviados.append((r["texto"], cid))
            db.marcar_enviado(r, silencio=SILENCIO)
        return enviados

    def test_madrugada_no_pedida_se_difiere_y_luego_llega_al_dueno(self):
        # Recordatorio de OTRO usuario, a las 02:00, hora NO explícita.
        db.add_recordatorio("2030-03-10T02:00", "entregar algo", dueno="999")

        # A las 02:05 NO debe entregarse nada (se difirió a la mañana).
        self.assertEqual(self._repartir(dt.datetime(2030, 3, 10, 2, 5)), [])

        # A las 07:30 ya vence y llega SOLO a su dueño (999), no a 123/456.
        enviados = self._repartir(dt.datetime(2030, 3, 10, 7, 30))
        self.assertEqual(enviados, [("entregar algo", "999")])

    def test_hora_explicita_suena_de_madrugada(self):
        db.add_recordatorio("2030-03-10T02:00", "alarma pedida",
                            dueno="999", hora_explicita=True)
        enviados = self._repartir(dt.datetime(2030, 3, 10, 2, 5))
        self.assertEqual(enviados, [("alarma pedida", "999")])

    def test_principal_llega_a_todos_sus_chats(self):
        db.add_recordatorio("2030-03-10T09:00", "reunión",
                            dueno=db.DUENO_PRINCIPAL, hora_explicita=True)
        enviados = self._repartir(dt.datetime(2030, 3, 10, 9, 1))
        self.assertEqual(sorted(c for _, c in enviados), ["123", "456"])
        self.assertTrue(all(t == "reunión" for t, _ in enviados))

    def test_insistencia_descuenta_y_no_se_apaga(self):
        rid = db.add_recordatorio("2030-03-10T22:50", "tomar pastilla",
                                  dueno="999", hora_explicita=True)
        # Insiste cada 30 min, 2 veces. Tras la 1ª entrega NO se apaga
        # (enviado=0) y descuenta una insistencia (2 -> 1).
        self.assertTrue(db.configurar_insistencia(rid, 30, 2, dueno="999"))
        enviados = self._repartir(dt.datetime(2030, 3, 10, 22, 51))
        self.assertEqual(enviados, [("tomar pastilla", "999")])  # a su dueño
        with db.conn() as c:
            fila = dict(c.execute("SELECT enviado, insistir_veces "
                                  "FROM recordatorios WHERE id=?", (rid,)).fetchone())
        self.assertEqual(fila["enviado"], 0)         # sigue activo
        self.assertEqual(fila["insistir_veces"], 1)  # descontó una


if __name__ == "__main__":
    unittest.main()
