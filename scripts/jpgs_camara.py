"""Extrae el JPEG completo incrustado de cada ARW y arma hojas de contacto para revisar a ojo cuales estan rotos.
Uso:  python jpgs_camara.py CARPETA_ARW SALIDA_JPG [CARPETA_HOJAS]
"""
import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

Image.MAX_IMAGE_PIXELS = None
ARW = sys.argv[1]
OUT = sys.argv[2]
SP = sys.argv[3] if len(sys.argv) > 3 else OUT
os.makedirs(OUT, exist_ok=True)
thumbs = []
faltan = []
for fn in sorted(os.listdir(ARW)):
    if not fn.endswith('.ARW'):
        continue
    d = open(os.path.join(ARW, fn), 'rb').read()
    t = rs.Tiff(d[:300000])
    i0 = t.ifd(t.ifd0)
    so = 0
    for o in t.ints(i0[0x14A]):
        ifd = t.ifd(o)
        if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767]:
            so = t.ints(ifd[0x111])[0]
    found = [j for j in rs.find_jpegs(d[:so + 4096]) if rs.jpeg_kind(j[2], j[3]) == 'full']
    if not found:
        faltan.append(fn)
        print(fn, 'SIN JPEG completo')
        continue
    s, e, w, h = found[0]
    jb = d[s:e]
    open(os.path.join(OUT, fn[:-4] + '.JPG'), 'wb').write(jb)
    im = Image.open(io.BytesIO(jb))
    im.draft('RGB', (im.width // 8, im.height // 8))
    im = im.convert('RGB')
    im.thumbnail((360, 240))
    thumbs.append((fn[:-4], im))
for p in range(0, len(thumbs), 20):
    chunk = thumbs[p:p + 20]
    sheet = Image.new('RGB', (5 * 366, 4 * 262), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    for i, (nm, th) in enumerate(chunk):
        x, y = (i % 5) * 366 + 3, (i // 5) * 262 + 18
        sheet.paste(th, (x, y))
        dr.text((x + 2, y - 15), nm, fill=(255, 255, 0))
    sheet.save(os.path.join(SP, 'jpgs_%d.jpg' % (p // 20 + 1)), quality=88)
print('faltan', faltan)
