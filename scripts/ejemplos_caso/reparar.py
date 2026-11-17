"""Reparaciones de los eventos de escritura fallida:  recoloca el final de la foto (alineado a la derecha) y rellena el hueco.
Cada reparacion produce D:\\RESCATE_SD\\REPARADOS\\DSCxxxxx.ARW  y deja constancia en reparaciones.json
Uso (venv):  reparar.py DSC01437 [DSC01440 ...]
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs
import sintesis as S

CS = 32768
W = 9600
IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
SRC = r'D:\RESCATE_SD\REARMADOS'
OUT = r'D:\RESCATE_SD\REPARADOS'
SP = r'TRABAJO'
os.makedirs(OUT, exist_ok=True)
f = open(IMG, 'rb')


def phys(p, n):
    f.seek(DATA + (p - 2) * CS)
    return f.read(n * CS)


# nombre: (k_fin_de_lo_bueno, inicio_fisico_del_final_bueno, clusters_del_final_bueno, JPEG de referencia o None=del propio archivo)
EVENTOS = {
    'DSC01437': dict(k_g1_end=1651, g2_phys=939227, g2_len=327, so=5459968, ncl=2035, size=66670592, jpeg=None),
    'DSC01440': dict(k_g1_end=1660, g2_phys=945348, g2_len=336, so=5734400, ncl=2043, size=66945024,
                     jpeg=r'D:\RESCATE_SD\refs2\H944407.jpg'),
}


def reparar(name):
    e = EVENTOS[name]
    s = open(os.path.join(SRC, name + '.ARW'), 'rb').read()
    so = e['so']
    ncl = e['ncl']
    hole = ncl - e['k_g1_end'] - e['g2_len']
    new = bytearray(s[:e['k_g1_end'] * CS])
    new += bytes(hole * CS)
    new += phys(e['g2_phys'], e['g2_len'])
    assert len(new) == ncl * CS, (len(new), ncl * CS)
    new = bytearray(new[:e['size']])
    # filas afectadas
    b0 = e['k_g1_end'] * CS
    b1 = (e['k_g1_end'] + hole) * CS
    r0 = (b0 - so) // W
    r1 = -((so - b1) // W)                 # ceil((b1-so)/W)
    # jpeg
    if e['jpeg']:
        jb = open(e['jpeg'], 'rb').read()
    else:
        full = [j for j in rs.find_jpegs(bytes(new[:so + 4096])) if rs.jpeg_kind(j[2], j[3]) == 'full'][0]
        jb = bytes(new[full[0]:full[1]])
    rep = S.fill_rows(new, so, jb, r0, r1)
    path = os.path.join(OUT, name + '.ARW')
    open(path, 'wb').write(bytes(new))
    info = dict(archivo=name + '.ARW', hueco_clusters=hole, filas_sinteticas=[int(r0), int(r1)], filas_totales=int(r1 - r0),
                R2=[round(x, 3) for x in rep], metodo='filas faltantes sintetizadas desde el JPEG de la camara')
    print(name, json.dumps(info))
    return info


log = {}
for nm in sys.argv[1:]:
    log[nm] = reparar(nm)
p = os.path.join(SP, 'reparaciones.json')
old = json.load(open(p)) if os.path.exists(p) else {}
old.update(log)
json.dump(old, open(p, 'w'), indent=1)
