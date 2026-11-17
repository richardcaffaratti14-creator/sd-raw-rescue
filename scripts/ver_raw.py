"""Decodifica ARW/SR2 con LibRaw (rawpy) y guarda una vista JPEG chica de cada uno.
Uso:  python ver_raw.py SALIDA_DIR archivo1 archivo2 ...
Si un RAW esta roto, la vista muestra las franjas/colores raros.
"""
import os
import sys
import time

import numpy as np
import rawpy
from PIL import Image

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for p in sys.argv[2:]:
    t0 = time.time()
    name = os.path.splitext(os.path.basename(p))[0]
    try:
        with rawpy.imread(p) as raw:
            rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
        im = Image.fromarray(rgb)
        im.thumbnail((1100, 1100))
        dst = os.path.join(out, name + '_raw.jpg')
        im.save(dst, quality=85)
        print('OK   %-40s %dx%d -> %s  (%.1fs)' % (os.path.basename(p), rgb.shape[1], rgb.shape[0], dst, time.time() - t0))
    except Exception as ex:
        print('FALLA %-40s %s' % (os.path.basename(p), ex))
