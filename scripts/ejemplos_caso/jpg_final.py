"""JPG finales: el de la camara cuando esta sano; generado desde el RAW cuando el de la camara esta roto o no existe.   (venv)"""
import csv
import json
import os
import shutil
import sys
import time

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs

Image.MAX_IMAGE_PIXELS = None
BASE = r'D:\RESCATE_SD'
FINAL = os.path.join(BASE, 'RECUPERADO_FINAL')
ARW = os.path.join(FINAL, 'RAW_Sony_ARW')
JPG = os.path.join(FINAL, 'JPG_camara')
ALL = os.path.join(BASE, 'jpg_camara_all')
ROTOS = {'DSC01400', 'DSC01438', 'DSC01441', 'DSC01443', 'DSC01444'}     # JPEG de la camara roto o inexistente
rep = json.load(open(os.path.join(BASE, 'logs', 'reporte_final.json'), encoding='utf-8'))
by = {r['archivo']: r for r in rep}
for fn in sorted(os.listdir(ARW)):
    if not fn.endswith('.ARW'):
        continue
    nm = fn[:-4]
    out = os.path.join(JPG, nm + '.JPG')
    d = open(os.path.join(ARW, fn), 'rb').read(300000)
    t = rs.Tiff(d)
    fecha = t.info().get('date', '') if t.ok else ''
    if nm in ROTOS:
        with rawpy.imread(os.path.join(ARW, fn)) as r:
            full = r.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        full = np.ascontiguousarray(full[20:20 + 6336, 32:32 + 9504])
        Image.fromarray(full).save(out, quality=92)
        by[fn]['jpg'] = 'generado desde el RAW (el JPEG de la camara estaba roto o no existe)'
        print(nm, 'generado', full.shape, flush=True)
    else:
        shutil.copyfile(os.path.join(ALL, nm + '.JPG'), out)
        by[fn]['jpg'] = 'JPEG de la camara'
    try:
        ts = time.mktime(time.strptime(fecha, '%Y:%m:%d %H:%M:%S'))
        os.utime(out, (ts, ts))
    except Exception:
        pass
json.dump(rep, open(os.path.join(BASE, 'logs', 'reporte_final.json'), 'w'), indent=1, ensure_ascii=False)
with open(os.path.join(FINAL, 'REPORTE.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(rep[0].keys()), delimiter=';')
    w.writeheader()
    w.writerows(rep)
print('FIN')
