"""QA de fotos rearmadas: ubica filas del RAW contra el JPEG embebido y reporta tramos con desplazamiento != 0 o datos ajenos.
Uso:  verificar_filas.py carpeta [nombres...]
Salida por foto:  tramos (fila_ini-fila_fin: desplazamiento en filas / AJENO)
"""
import io
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

Image.MAX_IMAGE_PIXELS = None
CS = 32768
W = 9600
OVERRIDE_SO = {}          # {'DSCnnnnn': so} para fotos con la cabecera pisada (so = tamano - 1024 - 61209600)
H = 6376
RANGE = 48          # busqueda +-48 filas
STEP = 16


def geometry(d, name):
    t = rs.Tiff(d[:2_000_000])
    if t.ok:
        i0 = t.ifd(t.ifd0)
        for o in (t.ints(i0[0x14A]) if 0x14A in i0 else []):
            ifd = t.ifd(o)
            if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
                return t.ints(ifd[0x111])[0]
    return OVERRIDE_SO.get(name)


def full_jpeg(d, so):
    head = d[:so + 4096]
    pos = 0
    while True:
        s = head.find(b'\xff\xd8\xff', pos)
        if s < 0:
            return None
        end, w, h = rs.jpeg_scan(head, s)
        if w == 9504 and h == 6336:
            return head[s:end] if end else head[s:so]
        pos = s + 3


def analizar(d, so, jb):
    im = Image.open(io.BytesIO(jb))
    im.draft('L', (4752 * 2, 3168 * 2))
    im = im.convert('L')
    Hj = 6336
    Jm = np.asarray(im.resize((297, Hj), Image.BOX), dtype=np.float32)
    Jm = Jm - Jm.mean(axis=1, keepdims=True)
    Jn = Jm / np.maximum(np.linalg.norm(Jm, axis=1, keepdims=True), 1e-6)
    rows = min(H, (len(d) - so) // W)
    buf = np.frombuffer(d, dtype=np.uint8, count=rows * W, offset=so)
    first = buf.view('<u4')[::4]
    mid = (((first & 0x7FF).astype(np.float32)) + (((first >> 11) & 0x7FF).astype(np.float32))).reshape(rows, 600) / 2
    out = []
    for r in range(60, rows - 60, STEP):
        g = mid[r, 1::2] if r % 2 == 0 else mid[r, 0::2]
        v = np.sqrt(np.maximum(g, 0))[1:297]
        v = v - v.mean()
        nv = np.linalg.norm(v)
        if nv < 1e-6:
            out.append((r, None, 0.0))
            continue
        v = v / nv
        y0 = r - 20
        lo, hi = max(0, y0 - RANGE), min(Hj, y0 + RANGE + 1)
        cand = Jn[lo:hi, :296]
        cand = cand - cand.mean(axis=1, keepdims=True)
        sc = cand @ v / np.maximum(np.linalg.norm(cand, axis=1), 1e-6)
        k = int(np.argmax(sc))
        out.append((r, (lo + k) - y0, float(sc[k])))
    return rows, out


def resumir(out):
    pts = [o for o in out if o[1] is not None]
    segs = []
    for i in range(len(pts)):
        w = pts[max(0, i - 3):i + 4]
        d = float(np.median([x[1] for x in w]))
        c = float(np.median([x[2] for x in w]))
        tag = 'AJENO' if c < 0.80 else ('SHIFT%+d' % int(round(d)) if abs(d) >= 6 else 'ok')
        r = pts[i][0]
        if segs and segs[-1][2] == tag:
            segs[-1][1] = r
        else:
            segs.append([r, r, tag])
    return segs


if __name__ == '__main__':
    folder = sys.argv[1]
    only = set(sys.argv[2:])
    for fn in sorted(os.listdir(folder)):
        if not fn.upper().endswith('.ARW'):
            continue
        name = os.path.splitext(fn)[0]
        if only and name not in only:
            continue
        d = open(os.path.join(folder, fn), 'rb').read()
        so = geometry(d, name)
        if not so:
            print('%s: sin geometria' % name)
            continue
        jb = full_jpeg(d, so)
        if not jb:
            print('%s: sin JPEG full' % name)
            continue
        try:
            rows, out = analizar(d, so, jb)
        except Exception as ex:
            print('%s: error %s' % (name, ex))
            continue
        segs = resumir(out)
        bad = [s for s in segs if s[2] != 'ok']
        txt = '  '.join('%d-%d:%s' % (a, b, t) for a, b, t in bad)
        print('%s %s  %s' % (name, 'OK ' if not bad else 'REV', txt if bad else '(todas las filas alineadas con el JPEG)'))
