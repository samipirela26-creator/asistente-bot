# -*- coding: utf-8 -*-
"""
Salud de la maquina donde corre el bot (pensado para la Lenovo).
Solo stdlib: lee /proc y /sys. estado_texto() -> mensaje HTML para Telegram.
"""

import os
import shutil
import datetime


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for linea in f:
                k, v = linea.split(":", 1)
                info[k] = int(v.strip().split()[0])  # kB
    except Exception:
        pass
    return info


def _uptime():
    try:
        with open("/proc/uptime") as f:
            seg = float(f.read().split()[0])
        d, r = divmod(int(seg), 86400)
        h, r = divmod(r, 3600)
        return f"{d}d {h}h {r // 60}m"
    except Exception:
        return "?"


def _temperatura():
    """Busca la zona termica mas caliente (CPU)."""
    mejor = None
    try:
        base = "/sys/class/thermal"
        for z in os.listdir(base):
            if not z.startswith("thermal_zone"):
                continue
            try:
                with open(f"{base}/{z}/temp") as f:
                    t = int(f.read().strip()) / 1000.0
                if 1 < t < 120 and (mejor is None or t > mejor):
                    mejor = t
            except Exception:
                continue
    except Exception:
        pass
    return mejor


def _carga():
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def estado_texto():
    """Reporte de salud en HTML simple para Telegram."""
    out = ["💻 <b>Estado de la maquina</b>"]

    mem = _meminfo()
    if mem:
        total = mem.get("MemTotal", 0) / 1048576.0     # GB
        disp = mem.get("MemAvailable", 0) / 1048576.0
        usada = total - disp
        pct = (usada / total * 100) if total else 0
        icono = "🟢" if pct < 75 else ("🟡" if pct < 90 else "🔴")
        out.append(f"  {icono} RAM: {usada:.1f}/{total:.1f} GB ({pct:.0f}%)")

    try:
        du = shutil.disk_usage("/")
        pct = du.used / du.total * 100
        icono = "🟢" if pct < 80 else ("🟡" if pct < 92 else "🔴")
        out.append(f"  {icono} Disco: {du.used / 1e9:.1f}/{du.total / 1e9:.1f} GB ({pct:.0f}%)")
    except Exception:
        pass

    carga = _carga()
    if carga is not None:
        nucleos = os.cpu_count() or 1
        pct = carga / nucleos * 100
        icono = "🟢" if pct < 70 else ("🟡" if pct < 100 else "🔴")
        out.append(f"  {icono} CPU: carga {carga:.2f} de {nucleos} nucleos ({pct:.0f}%)")

    t = _temperatura()
    if t is not None:
        icono = "🟢" if t < 70 else ("🟡" if t < 85 else "🔴")
        out.append(f"  {icono} Temperatura: {t:.0f}°C")

    out.append(f"  ⏱ Encendida desde hace: {_uptime()}")
    out.append(f"  🕐 Hora local: {datetime.datetime.now().strftime('%H:%M')}")
    out.append("  🤖 El bot esta vivo (si lees esto, funciona 😉)")
    return "\n".join(out)


def alertas():
    """Lista de problemas criticos, vacia si todo bien (para avisos automaticos)."""
    avisos = []
    mem = _meminfo()
    if mem:
        total, disp = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        if total and disp / total < 0.08:
            avisos.append("🔴 RAM casi llena (queda <8%)")
    try:
        du = shutil.disk_usage("/")
        if du.free / du.total < 0.07:
            avisos.append(f"🔴 Disco casi lleno (quedan {du.free / 1e9:.1f} GB)")
    except Exception:
        pass
    t = _temperatura()
    if t is not None and t >= 88:
        avisos.append(f"🔴 Temperatura alta: {t:.0f}°C")
    return avisos


if __name__ == "__main__":
    print(estado_texto().replace("<b>", "").replace("</b>", ""))
    print("Alertas:", alertas() or "ninguna")
