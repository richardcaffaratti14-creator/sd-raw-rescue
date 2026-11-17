"""Vuelve a sintetizar las filas de relleno de las fotos ya reparadas con el metodo automatico (regresion o tabla por canal,
el que mejor reproduce filas buenas vecinas).  Es idempotente: las filas a rellenar se reescriben desde el JPEG.   (venv)
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs
import sintesis as S

REP = r'D:\RESCATE_SD\REPARADOS'
BAK = r'D:\RESCATE_SD\REPARADOS_regresion'
os.makedirs(BAK, exist_ok=True)
R2 = r'D:\RESCATE_SD\refs2'
PLAN = [
    ('DSC01437', 5459968, [(5066, 5262)], None),
    ('DSC01440', 5734400, [(5068, 5230)], os.path.join(R2, 'H944407.jpg')),
    ('DSC01445', 4231168, [(5771, 6376)], os.path.join(R2, 'H953865.jpg')),
    ('DSC01446', 6201344, [(0, 693), (2647, 2669)], os.path.join(R2, 'H956629.jpg')),
    ('DSC01432', 5345280, [(5901, 5910)], os.path.join(R2, 'H928242.jpg')),
]
only = sys.argv[1:]
log = {}
for name, so, rngs, jp in PLAN:
    if only and name not in only:
        continue
    src = os.path.join(REP, name + '.ARW')
    if not os.path.exists(os.path.join(BAK, name + '.ARW')):
        shutil.copy2(src, os.path.join(BAK, name + '.ARW'))
    buf = bytearray(open(src, 'rb').read())
    if jp:
        jb = open(jp, 'rb').read()
    else:
        full = [j for j in rs.find_jpegs(bytes(buf[:so + 4096])) if rs.jpeg_kind(j[2], j[3]) == 'full'][0]
        jb = bytes(buf[full[0]:full[1]])
    jpg = S.Jpg(jb)
    info = []
    for r0, r1 in rngs:
        rep = S.fill_rows(buf, so, None, r0, r1, jpg=jpg)
        info.append(dict(filas=[r0, r1], **S.fill_rows.info))
        print(name, r0, r1, S.fill_rows.info)
    open(src, 'wb').write(bytes(buf))
    log[name] = info
json.dump(log, open(r'D:\RESCATE_SD\logs\resintesis.json', 'w'), indent=1)
