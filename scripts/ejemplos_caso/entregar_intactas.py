"""Saca de la imagen de la SD las fotos RAW que se pueden entregar SIN reparaciones, con el nombre original de la camara.
Usa el directorio y la FAT que la camara volco dentro de la zona de datos y deshace el intercambio de bloques de 12,5 MB.
Cada archivo se verifica: estructura TIFF y continuidad fila a fila del RAW.
"""
import csv
import os
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

IMG = r'D:\RESCATE_SD\sd_E.img'
OUT = r'D:\RESCATE_SD\RAW_RECUPERADOS'
DATA = 8388608
CS = 32768
U = 383
O = 924673
NU = 38          # unidades intercambiadas de a pares verificadas (hasta el primer volcado de metadatos)
f = open(IMG, 'rb')
os.makedirs(OUT, exist_ok=True)


def read_phys(p, n):
    f.seek(DATA + (p - 2) * CS)
    return f.read(n * CS)


def phys(x):
    if O <= x < O + NU * U:
        k, r = divmod(x - O, U)
        return O + U * (k ^ 1) + r
    return x


FATM = np.load(r'TRABAJO\fat_merged.npy')


def chain_of(c):
    out, x = [], c
    while 2 <= x < len(FATM) and len(out) < 100000:
        out.append(x)
        v = int(FATM[x])
        if v >= 0x0FFFFFF8:
            return out, 'EOC'
        if v == 0 or v >= len(FATM):
            return out, 'ROTA'
        x = v
    return out, 'BAD'


def read_logical(c, n):
    cl, fin = chain_of(c)
    if fin != 'EOC' or len(cl) != n:
        cl = list(range(c, c + n))
    parts, i = [], 0
    while i < len(cl):
        p = phys(cl[i])
        run = 1
        while i + run < len(cl) and cl[i + run] == cl[i + run - 1] + 1 and phys(cl[i + run]) == p + run:
            run += 1
        parts.append(read_phys(p, run))
        i += run
    return b''.join(parts)


def fat_dt(d, t):
    return time.mktime((1980 + (d >> 9), (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63, (t & 31) * 2, 0, 0, -1))


def dir_entries():
    ents = {}
    for c, s0 in ((939560, 48), (945688, 26), (956628, 58)):
        for s in range(s0, s0 + 3):
            f.seek(DATA + (c - 2) * CS + s * 512)
            sec = f.read(512)
            for i in range(0, 512, 32):
                e = sec[i:i + 32]
                if e[0] in (0, 0xE5) or e[8:11] != b'ARW' or e[11] != 0x20:
                    continue
                name = e[:8].decode('latin-1')
                cl = (struct.unpack_from('<H', e, 20)[0] << 16) | struct.unpack_from('<H', e, 26)[0]
                sz = struct.unpack_from('<I', e, 28)[0]
                wd, wt = struct.unpack_from('<H', e, 24)[0], struct.unpack_from('<H', e, 22)[0]
                ents[name] = (cl, sz, fat_dt(wd, wt))
    return ents


def raw_cost_profile(d):
    t = rs.Tiff(d)
    i0 = t.ifd(t.ifd0)
    for o in t.ints(i0[0x14A]) if 0x14A in i0 else []:
        ifd = t.ifd(o)
        if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
            so, sc, W, H = t.ints(ifd[0x111])[0], t.ints(ifd[0x117])[0], t.ints(ifd[0x100])[0], t.ints(ifd[0x101])[0]
            rows = min(H, (len(d) - so) // W)
            buf = np.frombuffer(d, dtype=np.uint8, count=rows * W, offset=so)
            first = buf.view('<u4')[::4]
            mx = (first & 0x7FF).astype(np.int32).reshape(rows, W // 16)
            mn = ((first >> 11) & 0x7FF).astype(np.int32).reshape(rows, W // 16)
            cost = np.abs(mx[2:] - mx[:-2]).mean(axis=1) + np.abs(mn[2:] - mn[:-2]).mean(axis=1)
            med = float(np.median(cost))
            real = 0
            last = -10
            for r in np.where(cost > 300)[0]:
                lo, hi = max(0, r - 40), min(len(cost), r + 40)
                loc = float(np.median(np.r_[cost[lo:max(lo, r - 2)], cost[r + 3:hi]])) if hi - lo > 6 else med
                if cost[r] > 6 * max(loc, 5) and r - last > 3:
                    real += 1
                last = r
            return rows, med, float(cost.max()), real, so + sc
    return 0, 0.0, 0.0, 0, 0


ents = dir_entries()
print('entradas de directorio: %d  (%s .. %s)' % (len(ents), min(ents), max(ents)))
rows = []
for name in sorted(ents):
    if int(name[3:]) > 1437:
        continue
    c, size, mt = ents[name]
    n = (size + CS - 1) // CS
    d = read_logical(c, n)[:size]
    ok_hdr = d[:4] == b'II*\x00'
    nrows, med, mx, spikes, endraw = raw_cost_profile(d) if ok_hdr else (0, 0, 0, 99, 0)
    good = ok_hdr and nrows == 6376 and spikes == 0 and endraw + 1024 == size
    status = 'OK' if good else 'REVISAR'
    path = os.path.join(OUT if good else os.path.join(OUT, '_con_problemas'), name + '.ARW')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as g:
        g.write(d)
    os.utime(path, (mt, mt))
    rows.append([name + '.ARW', c, size, status, nrows, round(med, 1), round(mx, 1), spikes, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt))])
    print('%s  cluster %-7d %9d bytes  %-8s filas=%d mediana=%.1f max=%.0f saltos=%d' % (name, c, size, status, nrows, med, mx, spikes))

with open(os.path.join(OUT, 'reporte_intactas.csv'), 'w', newline='', encoding='utf-8-sig') as g:
    w = csv.writer(g)
    w.writerow(['archivo', 'cluster_inicio', 'bytes', 'estado', 'filas_raw', 'costo_mediano', 'costo_max', 'saltos_sospechosos', 'fecha_camara'])
    w.writerows(rows)
print('OK: %d   REVISAR: %d' % (sum(1 for r in rows if r[3] == 'OK'), sum(1 for r in rows if r[3] != 'OK')))
