#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_jpg.py - revela un RAW con LibRaw (rawpy) y lo guarda como JPG. Para cuando el JPEG que la camara guarda dentro
del RAW esta roto o no existe (fotos reparadas / armadas con cabecera prestada).

Uso:  python render_jpg.py ENTRADA.ARW SALIDA.JPG [--crop auto|none|X,Y,W,H] [--mitad] [--calidad 92]
  --crop auto   A7R V: LibRaw devuelve 9564x6376 (con margenes); la camara recorta 32,20 -> 9504x6336 (DefaultCrop).
                Con otro tamano no recorta. X,Y,W,H se dan SIEMPRE en pixeles de resolucion completa.
  --mitad       revela a media resolucion (4x mas rapido; sirve para hojas de contacto y para revisar).
El resultado NO tiene el estilo de imagen de la camara (revelado basico, balance de blancos de la camara).
Pide: pip install rawpy numpy pillow
"""
import argparse
import os
import sys
import time

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

Image.MAX_IMAGE_PIXELS = None


def main():
    ap = argparse.ArgumentParser(description='Revela un RAW a JPG con LibRaw.')
    ap.add_argument('entrada')
    ap.add_argument('salida')
    ap.add_argument('--crop', default='auto')
    ap.add_argument('--mitad', action='store_true')
    ap.add_argument('--calidad', type=int, default=92)
    a = ap.parse_args()
    with rawpy.imread(a.entrada) as raw:
        rgb = raw.postprocess(half_size=a.mitad, use_camera_wb=True, no_auto_bright=False, output_bps=8)
    h, w = rgb.shape[:2]
    crop = None
    if a.crop == 'auto':
        if (h, w) == ((3188, 4782) if a.mitad else (6376, 9564)):
            crop = (32, 20, 9504, 6336)
    elif a.crop != 'none':
        crop = tuple(int(v) for v in a.crop.split(','))
    if crop:
        x, y, cw, ch = [v // 2 for v in crop] if a.mitad else crop
        rgb = np.ascontiguousarray(rgb[y:y + ch, x:x + cw])
    Image.fromarray(rgb).save(a.salida, quality=a.calidad)
    try:
        with open(a.entrada, 'rb') as f:
            t = rs.Tiff(f.read(300000))
        ts = time.mktime(time.strptime(t.info().get('date', ''), '%Y:%m:%d %H:%M:%S'))
        os.utime(a.salida, (ts, ts))
    except Exception:
        pass
    print('%s -> %s  %dx%d%s' % (a.entrada, a.salida, rgb.shape[1], rgb.shape[0], '  (recortado %s)' % (crop,) if crop else ''))


if __name__ == '__main__':
    main()
