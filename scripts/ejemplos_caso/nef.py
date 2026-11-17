"""Recupera los 8 NEF (Nikon D7200, nov-2021) que quedaron en la tarjeta: cada uno esta contiguo desde su cabecera.   (venv)
Los nombres originales se perdieron con el directorio: se nombran por la hora de disparo del EXIF.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs

CS, DATA = 32768, 8388608
IMG = r'D:\RESCATE_SD\sd_E.img'
OUT = r'D:\RESCATE_SD\NEF_2021'
os.makedirs(OUT, exist_ok=True)
LAST = 979393                                  # ultimo cluster de datos de la tarjeta
f = open(IMG, 'rb')
HEADS = [972729, 973540, 974347, 975161, 975970, 976791, 977645, 978638]
res = []
seen = {}
for c0 in HEADS:
    f.seek(DATA + (c0 - 2) * CS)
    head = f.read(8 << 20)
    t = rs.Tiff(head)
    inf = t.info()
    need = inf['need']
    ncl = -(-need // CS)
    disp = min(ncl, LAST - c0 + 1)             # clusters que realmente existen en la tarjeta
    f.seek(DATA + (c0 - 2) * CS)
    d = f.read(disp * CS)
    trunc = len(d) < need
    d = d[:need]
    fe = inf['date']                           # 2021:11:27 02:24:19
    base = 'D7200_' + fe.replace(':', '').replace(' ', '_') if fe else 'D7200_c%d' % c0
    k = seen.get(base, 0)
    seen[base] = k + 1
    name = base + ('' if k == 0 else '_' + 'bcdefgh'[k - 1]) + '.NEF'
    p = os.path.join(OUT, name)
    open(p, 'wb').write(d)
    try:
        ts = time.mktime(time.strptime(fe, '%Y:%m:%d %H:%M:%S'))
        os.utime(p, (ts, ts))
    except Exception:
        pass
    res.append(dict(archivo=name, cluster=c0, bytes_esperados=need, bytes_en_tarjeta=len(d), truncado=bool(trunc), fecha=fe))
    print('%-34s cluster %d  %d bytes%s' % (name, c0, len(d), '  TRUNCADO (esperado %d)' % need if trunc else ''))

import rawpy
from PIL import Image
for r in res:
    p = os.path.join(OUT, r['archivo'])
    try:
        with rawpy.imread(p) as raw:
            rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
        r['decodifica'] = True
        Image.fromarray(rgb).save(p[:-4] + '_vista.jpg', quality=88)
        print('  OK   ', r['archivo'], rgb.shape)
    except Exception as e:
        r['decodifica'] = False
        r['error'] = str(e)[:120]
        print('  FALLA', r['archivo'], str(e)[:120])
json.dump(res, open(os.path.join(OUT, 'reporte_nef.json'), 'w'), indent=1, ensure_ascii=False)
