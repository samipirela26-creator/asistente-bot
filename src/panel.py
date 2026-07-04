# -*- coding: utf-8 -*-
"""
Panel de estado en consola para el Lenovo (headless).
Se ve en la pantalla fisica (tty1): RAM, CPU, disco, temperatura y el bot,
con colores y barras. Solo stdlib. Liviano: lee /proc cada pocos segundos.

Uso:
    python3 panel.py            # bucle, refresca en pantalla (para el servicio)
    python3 panel.py --once     # imprime una sola vez (para probar)
"""

import os
import sys
import time
import shutil
import datetime
import subprocess

import sistema  # reutiliza la lectura de /proc y /sys ya existente

# --- Colores ANSI (sin librerias) ---
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
VERDE = "\033[92m"
AMAR = "\033[93m"
ROJO = "\033[91m"
CIAN = "\033[96m"
GRIS = "\033[90m"
AZUL = "\033[94m"
HOME = "\033[H"      # cursor arriba-izquierda (refresco sin parpadeo)
LIMPIA = "\033[2J"   # limpia pantalla
OCULTA = "\033[?25l"  # oculta el cursor
MUESTRA = "\033[?25h"

ANCHO = 30  # ancho de las barras


def _color(pct, v1, v2):
    """Verde < v1 <= amarillo < v2 <= rojo."""
    return VERDE if pct < v1 else (AMAR if pct < v2 else ROJO)


def _barra(pct, v1, v2):
    pct = max(0, min(100, pct))
    llenos = int(round(pct / 100 * ANCHO))
    col = _color(pct, v1, v2)
    return f"{col}{'█' * llenos}{GRIS}{'░' * (ANCHO - llenos)}{RESET}"


def _cpu_pct(intervalo=0.4):
    """Uso de CPU% midiendo /proc/stat en dos instantes."""
    def leer():
        with open("/proc/stat") as f:
            partes = f.readline().split()[1:]
        nums = [int(x) for x in partes]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return sum(nums), idle
    try:
        t1, i1 = leer()
        time.sleep(intervalo)
        t2, i2 = leer()
        dt, di = t2 - t1, i2 - i1
        return (1 - di / dt) * 100 if dt > 0 else 0.0
    except Exception:
        return 0.0


def _bot_activo():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "agenda-bot.service"],
            capture_output=True, text=True, timeout=4)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _linea(etiqueta, pct, texto, v1, v2):
    col = _color(pct, v1, v2)
    return (f"  {CIAN}{etiqueta:<6}{RESET} {_barra(pct, v1, v2)} "
            f"{col}{pct:5.1f}%{RESET}  {DIM}{texto}{RESET}")


def render():
    L = []
    ahora = datetime.datetime.now()
    L.append(f"{BOLD}{AZUL}╔══════════════════════════════════════════════════════════╗{RESET}")
    L.append(f"{BOLD}{AZUL}║{RESET}   {BOLD}🤖 ASISTENTE · panel de estado{RESET}"
             f"        {DIM}{ahora.strftime('%Y-%m-%d %H:%M:%S')}{RESET}   {BOLD}{AZUL}║{RESET}")
    L.append(f"{BOLD}{AZUL}╚══════════════════════════════════════════════════════════╝{RESET}")
    L.append("")

    # RAM
    mem = sistema._meminfo()
    if mem:
        total = mem.get("MemTotal", 0) / 1048576.0
        disp = mem.get("MemAvailable", 0) / 1048576.0
        usada = total - disp
        pct = (usada / total * 100) if total else 0
        L.append(_linea("RAM", pct, f"{usada:.1f} / {total:.1f} GB", 75, 90))

    # CPU
    cpu = _cpu_pct()
    nucleos = os.cpu_count() or 1
    L.append(_linea("CPU", cpu, f"{nucleos} nucleos", 70, 90))

    # Disco
    try:
        du = shutil.disk_usage("/")
        pct = du.used / du.total * 100
        L.append(_linea("Disco", pct, f"{du.used/1e9:.0f} / {du.total/1e9:.0f} GB", 80, 92))
    except Exception:
        pass

    # Temperatura (escala 0-100 visual, alerta a 70/85)
    t = sistema._temperatura()
    if t is not None:
        col = _color(t, 70, 85)
        L.append(f"  {CIAN}{'Temp':<6}{RESET} {_barra(t, 70, 85)} "
                 f"{col}{t:5.0f}°C{RESET}")

    L.append("")
    vivo = _bot_activo()
    estado = f"{VERDE}● EN LINEA{RESET}" if vivo else f"{ROJO}● APAGADO{RESET}"
    L.append(f"  {CIAN}{'Bot':<6}{RESET} {estado}        "
             f"{DIM}encendido hace {sistema._uptime()}{RESET}")

    # Alertas criticas, si las hay
    avisos = sistema.alertas()
    if avisos:
        L.append("")
        for a in avisos:
            L.append(f"  {ROJO}{a}{RESET}")

    L.append("")
    L.append(f"  {GRIS}Actualiza cada pocos segundos · headless · Ctrl+C para salir{RESET}")
    return "\n".join(L)


def main():
    if "--once" in sys.argv:
        print(render())
        return
    intervalo = 3
    try:
        sys.stdout.write(LIMPIA + OCULTA)
        while True:
            # \033[H reposiciona arriba: refresco estable sin parpadeo feo
            sys.stdout.write(HOME + render() + "\033[J")
            sys.stdout.flush()
            time.sleep(intervalo)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(MUESTRA + RESET + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
