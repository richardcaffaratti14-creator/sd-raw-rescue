"""Ubica filas del RAW dentro de la imagen real usando el JPEG embebido como referencia.
Uso:  python ubicar_filas.py archivo.arw fila_desde fila_hasta [paso]
Para cada fila del RAW (canal verde, 300 columnas) busca la fila del JPEG (9504x6336, recorte 32,20) que mejor correlaciona.
Si el RAW esta bien ordenado el desplazamiento es constante; un salto indica filas perdidas o trozos cambiados de lugar.
"""
import io
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

p = sys.argv[1]
r0, r1 = int(sys.argv[2]), int(sys.argv[3])
step = int(sys.argv[4]) if len(sys.argv) > 4 else 2
d = open(p, 'rb').read()
t = rs.Tiff(d)
i0 = t.ifd(t.ifd0)
for o in t.ints(i0[0x14A]):
    ifd = t.ifd(o)
    if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
        so, sc, W, H = t.ints(ifd[0x111])[0], t.ints(ifd[0x117])[0], t.ints(ifd[0x100])[0], t.ints(ifd[0x101])[0]
full = [j for j in rs.find_jpegs(d) if rs.jpeg_kind(j[2], j[3]) == 'full']
if not full:
    sys.exit('sin JPEG full embebido')
s, e, w, h = full[0]
Image.MAX_IMAGE_PIXELS = None
im = Image.open(io.BytesIO(d[s:e])).convert('L')
# JPEG -> 300 columnas (32 px de ancho cada una) y todas las filas
J = np.asarray(im.resize((w // 32, h), Image.BILINEAR), dtype=np.float32)      # (6336, 297)
nb = W // 16
buf = np.frombuffer(d, dtype=np.uint8, count=H * W, offset=so)
first = buf.view('<u4')[::4]
mx = (first & 0x7FF).astype(np.float32).reshape(H, nb)
mn = ((first >> 11) & 0x7FF).astype(np.float32).reshape(H, nb)
mid = (mx + mn) / 2


def green(r):
    # filas pares: G en bloques impares; filas impares: G en bloques pares
    g = mid[r, 1::2] if r % 2 == 0 else mid[r, 0::2]
    return g           # 300 valores (uno por tramo de 32 columnas)


def prof(v):
    v = np.sqrt(np.maximum(v, 0))
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n else v


Jp = [prof(J[y, :]) for y in range(J.shape[0])]
cols = min(J.shape[1], 300) - 3          # recorte horizontal de 32 px = 1 tramo
print('%s  tira@%d  jpeg %dx%d  (fila_raw -> mejor fila_jpeg, desplazamiento = fila_raw - 20 - fila_jpeg)' % (p, so, w, h))
for r in range(r0, r1, step):
    g = prof(green(r)[1:1 + cols])
    best, by = -1, -1
    for y in range(max(0, r - 20 - 80), min(len(Jp), r - 20 + 80)):
        c = float(np.dot(g, Jp[y][:cols]) / max(np.linalg.norm(Jp[y][:cols]), 1e-6))
        if c > best:
            best, by = c, y
    print('  fila %5d -> jpeg %5d  desplaz=%4d  corr=%.3f' % (r, by, r - 20 - by, best))
