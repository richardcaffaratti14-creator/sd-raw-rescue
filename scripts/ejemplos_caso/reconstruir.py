"""Arma las fotos de la region danada desde el flujo logico (unidades + pares intercambiados) de flujo.py.
Escribe D:\\RESCATE_SD\\REARMADOS\\DSC014xx.ARW  (bruto, sin reparar huecos) y un indice JSON.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs

SP = r'TRABAJO'
IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
OUT = r'D:\RESCATE_SD\REARMADOS'
os.makedirs(OUT, exist_ok=True)
J = json.load(open(SP + r'\flujo.json'))
units = [tuple(u) for u in J['units']]
order = J['order']
lam = {int(k): v for k, v in J['lam'].items()}
f = open(IMG, 'rb')


def segs_for(l0, n):
    """segmentos fisicos (inicio, largo) que cubren los clusters logicos [l0, l0+n)"""
    out = []
    pos = l0
    end = min(l0 + n, max(lam[i] + (units[i][1] - units[i][0]) for i in lam))
    while pos < end:
        for i in order:
            a, b = units[i]
            ls = lam[i]
            le = ls + (b - a)
            if ls <= pos < le:
                take = min(end, le) - pos
                out.append((a + (pos - ls), take))
                pos += take
                break
        else:
            raise SystemExit('posicion logica %d fuera del flujo' % pos)
    return out


def read_segs(segs):
    parts = []
    for a, n in segs:
        f.seek(DATA + (a - 2) * CS)
        parts.append(f.read(n * CS))
    return b''.join(parts)


def unit_of(p):
    for i, (a, b) in enumerate(units):
        if a <= p < b:
            return i


def to_lam(p):
    i = unit_of(p)
    return lam[i] + (p - units[i][0])


# cabeceras (fisicas) -> numero de foto
HDR = [926204, 928242, 929508, 932280, 934315, 935583, 938285, 939561, 941597, 944407, 945688, 947731, 950544, 951829,
       953865, 956629, 957921, 959976, 962796, 964042, 966050, 968870, 970161]
names = {h: 'DSC%05d' % (1431 + i) for i, h in enumerate(HDR)}
DIRSZ = {'DSC01431': 66760704, 'DSC01432': 66555904, 'DSC01433': 65708032, 'DSC01434': 66662400, 'DSC01435': 66637824,
         'DSC01436': 63438848, 'DSC01437': 66670592, 'DSC01438': 66945024, 'DSC01439': 66977792, 'DSC01440': 66945024,
         'DSC01441': 67047424, 'DSC01442': 67076096, 'DSC01443': 67186688, 'DSC01444': 66686976, 'DSC01445': 65441792}
L = [(to_lam(h), h) for h in HDR]
L.sort()
index = {}
for k, (l, h) in enumerate(L):
    nxt = L[k + 1][0] if k + 1 < len(L) else None
    name = names[h]
    n_stream = (nxt - l) if nxt else None
    big = read_segs(segs_for(l, 40))
    t = rs.Tiff(big)
    need = None
    so = sc = None
    if t.ok:
        i0 = t.ifd(t.ifd0)
        for o in (t.ints(i0[0x14A]) if 0x14A in i0 else []):
            ifd = t.ifd(o)
            if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
                so, sc = t.ints(ifd[0x111])[0], t.ints(ifd[0x117])[0]
    size = DIRSZ.get(name) or ((so + sc + 1024) if so else None)
    if n_stream is None:
        n_stream = (size + CS - 1) // CS if size else 2000
    segs = segs_for(l, n_stream)
    data = read_segs(segs)
    if size:
        data = data[:size]
    p = os.path.join(OUT, name + '.ARW')
    open(p, 'wb').write(data)
    index[name] = dict(h=h, lam=l, n=n_stream, size=size, so=so, segs=segs, path=p)
    print('%s  header=%d  clusters=%d  bytes=%d  segmentos=%d  (%s)' % (name, h, n_stream, len(data), len(segs), data[:4]))
json.dump(index, open(SP + r'\rearmados.json', 'w'))
