"""Tarjeta de progreso en PNG — la dibuja la MÁQUINA (no la IA).

Genera una imagen 'estilo D': un anillo con el porcentaje global de avance más
barras por proyecto, alimentada por los datos de SQLite (db). Stdlib pura:
zlib + struct + una fuente bitmap propia. Sin Pillow, sin matplotlib.

Uso:
    png_bytes = tarjeta.generar(dueno=None)
"""
import datetime
import math
import struct
import zlib

# ----------------------------------------------------------------- writer PNG
def _chunk(tipo, datos):
    c = tipo + datos
    return struct.pack(">I", len(datos)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def _png(ancho, alto, pixeles):
    filas = bytearray()
    for y in range(alto):
        filas.append(0)  # filtro 'None'
        ini = y * ancho * 3
        filas.extend(pixeles[ini:ini + ancho * 3])
    comprimido = zlib.compress(bytes(filas), 9)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", comprimido)
            + _chunk(b"IEND", b""))


class _Lienzo:
    def __init__(self, ancho, alto, fondo):
        self.w = ancho
        self.h = alto
        self.px = bytearray(bytes(fondo) * (ancho * alto))

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(c)

    def rect(self, x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(self.h, y1)):
            base = y * self.w
            for x in range(max(0, x0), min(self.w, x1)):
                i = (base + x) * 3
                self.px[i:i + 3] = bytes(c)

    def bytes(self):
        return _png(self.w, self.h, self.px)


# -------------------------------------------------------- fuente bitmap 5x7
_F = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "Ñ": ["11101", "00000", "10001", "11001", "10101", "10011", "10001"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
}
_ACENTOS = {"Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ü": "U"}


def _texto(lz, s, x, y, c, esc=2):
    s = s.upper()
    cur = x
    for ch in s:
        ch = _ACENTOS.get(ch, ch)
        g = _F.get(ch, _F[" "])
        for fy, fila in enumerate(g):
            for fx, bit in enumerate(fila):
                if bit == "1":
                    lz.rect(cur + fx * esc, y + fy * esc,
                            cur + fx * esc + esc, y + fy * esc + esc, c)
        cur += 6 * esc
    return cur


def _ancho(s, esc=2):
    return len(s) * 6 * esc


# ------------------------------------------------------------------- paleta
_TINTA = (38, 50, 56)
_CREMA = (245, 242, 235)
_VERDE = (46, 125, 80)
_AMBAR = (191, 138, 40)
_GRIS = (120, 130, 135)
_HUECO = (225, 220, 210)


# --------------------------------------------------------------------- datos
def _datos(dueno, hoy):
    """Reúne lo que pinta la tarjeta desde la BD. Tolerante a BD vacía."""
    import db
    proyectos = []
    hechas_tot = total_tot = 0
    for p in db.cargar_proyectos(solo_pendientes=False, dueno=dueno):
        fases = p.get("fases", [])
        total = len(fases)
        hechas = sum(1 for f in fases if f.get("hecho"))
        hechas_tot += hechas
        total_tot += total
        if total:
            proyectos.append((p["nombre"], hechas / total, hechas, total))
    proyectos.sort(key=lambda t: t[1])  # los más atrasados primero
    pct = (hechas_tot / total_tot) if total_tot else 0.0
    racha = db.racha(dueno=dueno)
    hechos_hoy = len(db.actividad_de(dueno=dueno))
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    categorias = db.actividad_por_categoria(lunes, hoy, dueno=dueno)[:5]
    return {"pct": pct, "proyectos": proyectos[:5], "racha": racha,
            "hechos_hoy": hechos_hoy, "hoy": hoy, "categorias": categorias}


# ------------------------------------------------------------------ dibujo
def generar(dueno=None, hoy=None):
    """Devuelve los bytes de un PNG con la tarjeta de progreso."""
    hoy = hoy or datetime.date.today()
    d = _datos(dueno, hoy)
    W, H = 600, 460
    lz = _Lienzo(W, H, _CREMA)

    _texto(lz, "PROGRESO", 30, 26, _TINTA, 4)
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    rango = f"SEMANA DEL {lunes.day} AL {hoy.day}"
    _texto(lz, rango, 30, 78, _GRIS, 2)

    # Anillo de progreso global.
    cx, cy, rad = 150, 235, 88
    pct = max(0.0, min(1.0, d["pct"]))
    for y in range(cy - rad, cy + rad):
        for x in range(cx - rad, cx + rad):
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if rad - 18 <= dist <= rad:
                ang = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
                col = _VERDE if ang <= pct * 2 * math.pi else _HUECO
                lz.set(x, y, col)
    etq = f"{int(round(pct * 100))}%"
    _texto(lz, etq, cx - _ancho(etq, 3) // 2, cy - 10, _TINTA, 3)

    # Barras por proyecto (o aviso si no hay).
    bx, by = 300, 150
    if d["proyectos"]:
        for i, (nom, frac, hechas, total) in enumerate(d["proyectos"]):
            y = by + i * 38
            _texto(lz, nom[:9], bx, y, _TINTA, 2)
            lz.rect(bx + 120, y, bx + 120 + 150, y + 16, _HUECO)
            col = _VERDE if frac >= 0.6 else _AMBAR
            lz.rect(bx + 120, y, bx + 120 + int(150 * frac), y + 16, col)
            _texto(lz, f"{hechas}/{total}", bx + 120 + 158, y, _GRIS, 1)
    else:
        _texto(lz, "SIN PROYECTOS", bx, by + 20, _GRIS, 2)
        _texto(lz, "AUN", bx, by + 50, _GRIS, 2)

    # Avances de la semana agrupados por categoria (la IA las asigna al
    # capturar el parte nocturno; las fases usan el nombre del proyecto).
    cy2 = 340
    _texto(lz, "AVANCES POR CATEGORIA", 30, cy2, _TINTA, 2)
    if d["categorias"]:
        maximo = max(n for _, n in d["categorias"]) or 1
        for i, (cat, n) in enumerate(d["categorias"]):
            y = cy2 + 30 + i * 22
            _texto(lz, cat[:14], 30, y, _TINTA, 1)
            lz.rect(180, y, 180 + 320, y + 12, _HUECO)
            lz.rect(180, y, 180 + int(320 * n / maximo), y + 12, _VERDE)
            _texto(lz, str(n), 180 + 328, y, _GRIS, 1)
    else:
        _texto(lz, "SIN AVANCES ESTA SEMANA", 30, cy2 + 30, _GRIS, 2)

    # Pie: racha y actividad de hoy.
    pie = f"RACHA {d['racha']}D    HOY {d['hechos_hoy']}"
    _texto(lz, pie, 30, H - 34, _GRIS, 2)
    return lz.bytes()
