"""Fotos borradas del evento (sin entrada en el directorio):  DSC01401-03 (cabecera intacta)  y  DSC01400 (parcial).   (venv)
 DSC01400: la tarjeta nunca grabo sus primeros 401 clusters (cabecera + 12% superior de la imagen): son ceros.
           Se arma con la cabecera de DSC01399 + la tira RAW que si esta + filas negras arriba.
"""
import io
import os
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs
import sintesis as S

CS, W, DATA = 32768, 9600, 8388608
IMG = r'D:\RESCATE_SD\sd_E.img'
OUT = r'D:\RESCATE_SD\BORRADOS'
os.makedirs(OUT, exist_ok=True)
f = open(IMG, 'rb')


def clusters(c, n):
    f.seek(DATA + (c - 2) * CS)
    return f.read(n * CS)


def poner_fecha(path, fecha):
    try:
        t = time.mktime(time.strptime(fecha, '%Y:%m:%d %H:%M:%S'))
        os.utime(path, (t, t))
    except Exception:
        pass


# ---------------------------------------------------------------- DSC01401-03
for name, c0, ncl in (('DSC01401', 865103, 1938), ('DSC01402', 867041, 1967), ('DSC01403', 869008, 1970)):
    d = bytearray(clusters(c0, ncl))
    t = rs.Tiff(bytes(d[:4 << 20]))
    inf = t.info()
    size = inf['need'] + 1024
    assert size <= len(d) and size > len(d) - CS, (name, size, len(d))
    d = d[:size]
    p = os.path.join(OUT, name + '.ARW')
    open(p, 'wb').write(bytes(d))
    poner_fecha(p, inf['date'])
    print(name, 'cluster', c0, 'bytes', size, 'fecha', inf['date'])

# ---------------------------------------------------------------- DSC01400 (parcial)
SO, NCL, K0, C_FIRST = 5865472, 2047, 401, 863457
size = SO + 61209600 + 1024
assert size == NCL * CS
# DSC01400 es el cuadro anterior de la misma rafaga que DSC01401 (misma escena y ajustes): su cabecera es la mejor donante
donor = open(os.path.join(OUT, 'DSC01401.ARW'), 'rb').read()
td = rs.Tiff(donor[:300000])
i0d = td.ifd(td.ifd0)
prev_off = td.ints(i0d[0x201])[0]
prev_len = td.ints(i0d[0x202])[0]
fecha = td.info()['date'] or '2000:01:01 00:00:00'
print('donante DSC01401: preview en', prev_off, 'len', prev_len, 'fecha', fecha)

new = bytearray(size)
new[0:prev_off] = donor[0:prev_off]
tail = clusters(C_FIRST, NCL - K0)                     # clusters 401..2046 -> 863457..865102
new[K0 * CS: K0 * CS + len(tail)] = tail
# filas que faltan arriba: negras (codigo 0), validas para el decodificador
s0 = K0 * CS - SO                                       # primer byte de tira que si existe
r_full = -(-s0 // W)                                    # primera fila completa
print('tira conservada desde byte', s0, ' fila parcial', s0 // W, ' primera fila completa', r_full, '(%.1f%% de la imagen)' % (100 * r_full / 6376))
zeros_row = S.encode_rows(np.zeros((1, W), dtype=np.int32))
for r in range(0, s0 // W):
    new[SO + r * W: SO + (r + 1) * W] = zeros_row
# fila parcial: lo que falta de la fila se toma de la fila de igual color (r+2)
rp = s0 // W
if s0 % W:
    cut = s0 - rp * W
    new[SO + rp * W: SO + rp * W + cut] = new[SO + (rp + 2) * W: SO + (rp + 2) * W + cut]
# cabecera: posicion y largo de la tira + fechas
t = rs.Tiff(bytes(new[:300000]))
i0 = t.ifd(t.ifd0)
for o in t.ints(i0[0x14A]):
    ifd = t.ifd(o)
    if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767]:
        struct.pack_into('<I', new, ifd[0x111][2], SO)
        struct.pack_into('<I', new, ifd[0x117][2], 61209600)
p = os.path.join(OUT, 'DSC01400.ARW')
open(p, 'wb').write(bytes(new))
print('DSC01400 provisorio escrito', len(new))

# vista previa propia (dimensiones de la del donante) incrustada en la cabecera
import rawpy
from PIL import Image
pw, ph = None, None
for s_, e_, w_, h_ in rs.find_jpegs(donor[prev_off:prev_off + prev_len + 16]):
    pw, ph = w_, h_
    break
print('preview donante', pw, ph)
with rawpy.imread(p) as r:
    rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
im = Image.fromarray(rgb)
if pw and ph:
    im = im.resize((pw, ph), Image.LANCZOS)
space = SO - prev_off
for q in range(92, 40, -4):
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=q)
    if len(b.getvalue()) <= min(space - 64, 2_000_000):
        break
pv = b.getvalue()
new[prev_off:prev_off + len(pv)] = pv
struct.pack_into('<I', new, i0[0x202][2], len(pv))
open(p, 'wb').write(bytes(new))
poner_fecha(p, fecha)
print('DSC01400 final: vista previa %d bytes (q=%d)' % (len(pv), q))
