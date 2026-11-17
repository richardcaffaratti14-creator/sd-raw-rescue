"""Flujo logico de la region danada: unidades fisicas -> pares intercambiados -> fotos cortadas en sus cabeceras TIFF.
Verifica por suma de clusters el largo de cada foto.  (python normal o del venv)
"""
import json
import struct
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs

SP = r'TRABAJO'
IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
z = np.load(SP + r'\region.npz')
types, cost, c0 = z['types'], z['cost'], int(z['c0'])
T = lambda c: int(types[c - c0])
RAW = 1
f = open(IMG, 'rb')


def rd(p, n=1):
    f.seek(DATA + (p - 2) * CS)
    return f.read(n * CS)


# ---- limites fuertes
strong = set()
for c in range(924000, 972148):
    tp, tc = T(c - 1), T(c)
    if tp == RAW and tc == RAW and cost[c - 1 - c0] > 400:
        strong.add(c)
    elif tp != tc and (tp == RAW or tc == RAW or tc == 4):
        strong.add(c)

# ---- unidades
O = 924673
END = 972148
units = []
b = O
DUMPS = [(939554, 939567), (945684, 945694), (956625, 956629)]
prev_has_dump = False
while b < END:
    exp = b + 383
    nxt = exp
    if prev_has_dump:      # tras la unidad con volcado la reticula se corre (unidad corta)
        sp = [s for s in strong if exp - 12 <= s <= exp + 12 and T(s) == RAW and T(s - 1) == RAW]
        if sp:
            nxt = min(sp, key=lambda s: abs(s - exp))
    units.append((b, nxt))
    prev_has_dump = any(b <= d0 < nxt for d0, d1 in DUMPS)
    b = nxt
print('unidades: %d  ultima=%s' % (len(units), units[-1]))
short = [(i, u, u[1] - u[0]) for i, u in enumerate(units) if u[1] - u[0] != 383]
print('unidades que no miden 383:', short)

# ---- orden logico por pares (2m,2m+1) -> (2m+1,2m)
NOSWAP = set()       # pares (i, i+1) que quedaron sin intercambiar (se ve por la suma de clusters)
order = []
for i in range(0, len(units) - 1, 2):
    order += [i, i + 1] if i in NOSWAP else [i + 1, i]
if len(units) % 2:
    order.append(len(units) - 1)

# desplazamiento logico acumulado de cada unidad
lam = {}
acc = 0
for i in order:
    lam[i] = acc
    acc += units[i][1] - units[i][0]
total = acc


def unit_of(p):
    for i, (a, bb) in enumerate(units):
        if a <= p < bb:
            return i
    return None


def to_lam(p):
    i = unit_of(p)
    return None if i is None else lam[i] + (p - units[i][0])


# ---- cabeceras (TIFF) + las dos con la primera parte pisada por el volcado
hdr = sorted([c0 + int(i) for i in np.where(types == 4)[0] if O <= c0 + int(i) < END] + [939561, 945688])
print('cabeceras en el flujo: %d' % len(hdr))
rows = []
for h in hdr:
    rows.append((to_lam(h), h))
rows.sort()
print('foto(cabecera fisica) -> largo en flujo logico')
for (l, h), nxt in zip(rows, rows[1:] + [(total, None)]):
    n = nxt[0] - l
    buf = rd(h, 40)
    t = rs.Tiff(buf)
    ncl = None
    if t.ok:
        i0 = t.ifd(t.ifd0)
        for o in (t.ints(i0[0x14A]) if 0x14A in i0 else []):
            ifd = t.ifd(o)
            if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
                so, sc = t.ints(ifd[0x111])[0], t.ints(ifd[0x117])[0]
                ncl = (so + sc + 1024 + CS - 1) // CS
    print('  h=%d  lam=%d  largo=%d  esperado=%s  %s' % (h, l, n, ncl, '' if ncl is None else ('OK' if ncl == n else 'DIF %+d' % (n - ncl))))
json.dump(dict(units=units, order=order, lam={str(k): v for k, v in lam.items()}, hdr=hdr), open(SP + r'\flujo.json', 'w'))
