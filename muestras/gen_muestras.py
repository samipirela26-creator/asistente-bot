#!/usr/bin/env python3
"""Genera muestras de 'tarjeta de progreso' en PNG con stdlib pura.
Sin Pillow, sin matplotlib: solo zlib + struct + una fuente bitmap propia.
"""
import struct
import zlib

# ----------------------------------------------------------------------------
# Writer PNG minimal (RGB, sin alpha)
# ----------------------------------------------------------------------------
def _chunk(tipo, datos):
    c = tipo + datos
    return struct.pack(">I", len(datos)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def guardar_png(ruta, ancho, alto, pixeles):
    """pixeles: bytearray RGB de tamano ancho*alto*3."""
    filas = bytearray()
    for y in range(alto):
        filas.append(0)  # filtro 'None' por fila
        ini = y * ancho * 3
        filas.extend(pixeles[ini:ini + ancho * 3])
    comprimido = zlib.compress(bytes(filas), 9)
    with open(ruta, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0)))
        f.write(_chunk(b"IDAT", comprimido))
        f.write(_chunk(b"IEND", b""))


# ----------------------------------------------------------------------------
# Lienzo
# ----------------------------------------------------------------------------
class Lienzo:
    def __init__(self, ancho, alto, fondo=(255, 255, 255)):
        self.w = ancho
        self.h = alto
        self.px = bytearray(fondo * (ancho * alto))

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(c)

    def rect(self, x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.set(x, y, c)

    def rect_borde(self, x0, y0, x1, y1, c, grosor=1):
        self.rect(x0, y0, x1, y0 + grosor, c)
        self.rect(x0, y1 - grosor, x1, y1, c)
        self.rect(x0, y0, x0 + grosor, y1, c)
        self.rect(x1 - grosor, y0, x1, y1, c)

    def rect_redondeado(self, x0, y0, x1, y1, c, r=8):
        self.rect(x0 + r, y0, x1 - r, y1, c)
        self.rect(x0, y0 + r, x1, y1 - r, c)
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            for dy in range(-r, r):
                for dx in range(-r, r):
                    if dx * dx + dy * dy <= r * r:
                        self.set(cx + dx, cy + dy, c)

    def guardar(self, ruta):
        guardar_png(ruta, self.w, self.h, self.px)


# ----------------------------------------------------------------------------
# Fuente bitmap 5x7 (subconjunto: 0-9 A-Z y simbolos)
# ----------------------------------------------------------------------------
F = {
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
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
}


def texto(lz, s, x, y, c, esc=2):
    s = s.upper()
    cur = x
    for ch in s:
        g = F.get(ch, F[" "])
        for fy, fila in enumerate(g):
            for fx, bit in enumerate(fila):
                if bit == "1":
                    lz.rect(cur + fx * esc, y + fy * esc, cur + fx * esc + esc, y + fy * esc + esc, c)
        cur += 6 * esc
    return cur


def ancho_texto(s, esc=2):
    return len(s) * 6 * esc


# Paleta sobria estilo "Larry" (verde botella, crema, gris pizarra)
TINTA = (38, 50, 56)
CREMA = (245, 242, 235)
VERDE = (46, 125, 80)
VERDE_CL = (165, 214, 167)
AMBAR = (191, 138, 40)
GRIS = (120, 130, 135)
ROJO = (176, 78, 70)


# ============================================================================
# MUESTRA A — Barras por proyecto
# ============================================================================
def muestra_a(ruta):
    W, H = 600, 360
    lz = Lienzo(W, H, CREMA)
    lz.rect(0, 0, W, 54, VERDE)
    texto(lz, "RESUMEN SEMANAL", 24, 18, CREMA, 3)
    texto(lz, "DOMINGO 14 JUN", 24, 60, GRIS, 2)

    datos = [("CASA", 8, 10), ("ESTUDIO", 5, 12), ("BOT", 14, 14), ("SALUD", 3, 7)]
    base_x, base_y = 30, 110
    barra_h = 34
    sep = 56
    maxv = max(t for _, _, t in datos)
    for i, (nom, hecho, total) in enumerate(datos):
        y = base_y + i * sep
        texto(lz, nom, base_x, y, TINTA, 2)
        bx = base_x + 130
        bw = 380
        lz.rect(bx, y, bx + bw, y + barra_h, (225, 220, 210))
        llen = int(bw * hecho / maxv)
        lz.rect(bx, y, bx + llen, y + barra_h, VERDE if hecho >= total * 0.6 else AMBAR)
        texto(lz, f"{hecho}/{total}", bx + bw + 8, y + 8, TINTA, 2)
    lz.rect_borde(0, 0, W, H, VERDE, 3)
    lz.guardar(ruta)


# ============================================================================
# MUESTRA B — Tarjeta de numeros grandes
# ============================================================================
def muestra_b(ruta):
    W, H = 600, 360
    lz = Lienzo(W, H, TINTA)
    texto(lz, "SU SEMANA", 30, 28, CREMA, 4)
    texto(lz, "14 JUN 2026", 30, 78, GRIS, 2)

    celdas = [("HECHAS", "30", VERDE_CL), ("PENDIENTES", "9", AMBAR),
              ("RECORDADOS", "22", CREMA), ("RACHA DIAS", "6", VERDE_CL)]
    cw, ch = 260, 110
    gx, gy = 30, 120
    for i, (etq, num, col) in enumerate(celdas):
        cx = gx + (i % 2) * (cw + 20)
        cy = gy + (i // 2) * (ch + 16)
        lz.rect_redondeado(cx, cy, cx + cw, cy + ch, (52, 66, 73), 10)
        texto(lz, num, cx + 18, cy + 18, col, 6)
        texto(lz, etq, cx + 18, cy + 82, GRIS, 2)
    lz.guardar(ruta)


# ============================================================================
# MUESTRA C — Cuadricula de habitos (heatmap semanal estilo GitHub)
# ============================================================================
def muestra_c(ruta):
    W, H = 600, 320
    lz = Lienzo(W, H, CREMA)
    lz.rect(0, 0, W, 50, TINTA)
    texto(lz, "CONSTANCIA", 24, 16, CREMA, 3)

    dias = ["L", "M", "M", "J", "V", "S", "D"]
    filas = ["TAREAS", "AGENDA", "LECTURA"]
    niveles = [
        [3, 2, 4, 1, 3, 0, 2],
        [4, 4, 3, 4, 2, 1, 3],
        [1, 0, 2, 0, 1, 3, 0],
    ]
    escala = {0: (225, 220, 210), 1: VERDE_CL, 2: (102, 187, 106), 3: VERDE, 4: (27, 94, 32)}
    cs = 46
    ox, oy = 150, 90
    for d, etq in enumerate(dias):
        texto(lz, etq, ox + d * (cs + 6) + 14, oy - 26, GRIS, 2)
    for r, nom in enumerate(filas):
        y = oy + r * (cs + 12)
        texto(lz, nom, 20, y + 14, TINTA, 2)
        for d in range(7):
            x = ox + d * (cs + 6)
            lz.rect_redondeado(x, y, x + cs, y + cs, escala[niveles[r][d]], 6)
    lz.guardar(ruta)


# ============================================================================
# MUESTRA D — Anillo de progreso + barras finas (mixta)
# ============================================================================
def muestra_d(ruta):
    W, H = 600, 360
    lz = Lienzo(W, H, CREMA)
    texto(lz, "PROGRESO", 30, 26, TINTA, 4)
    texto(lz, "SEMANA DEL 8 AL 14", 30, 72, GRIS, 2)

    # anillo: dibujado por sectores (porcentaje)
    cx, cy, rad = 150, 230, 90
    pct = 0.78
    import math
    for y in range(cy - rad, cy + rad):
        for x in range(cx - rad, cx + rad):
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if rad - 18 <= dist <= rad:
                ang = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
                col = VERDE if ang <= pct * 2 * math.pi else (225, 220, 210)
                lz.set(x, y, col)
    n = "78%"
    texto(lz, n, cx - ancho_texto(n, 3) // 2, cy - 10, TINTA, 3)

    # barras finas a la derecha
    items = [("CASA", 0.8), ("BOT", 1.0), ("ESTUDIO", 0.45), ("SALUD", 0.6)]
    bx, by = 300, 150
    for i, (nom, p) in enumerate(items):
        y = by + i * 42
        texto(lz, nom, bx, y, TINTA, 2)
        lz.rect(bx + 110, y, bx + 110 + 160, y + 18, (225, 220, 210))
        lz.rect(bx + 110, y, bx + 110 + int(160 * p), y + 18, VERDE if p >= 0.6 else AMBAR)
    lz.guardar(ruta)


if __name__ == "__main__":
    muestra_a("muestra_A_barras.png")
    muestra_b("muestra_B_numeros.png")
    muestra_c("muestra_C_constancia.png")
    muestra_d("muestra_D_anillo.png")
    print("Generadas: A, B, C, D")
