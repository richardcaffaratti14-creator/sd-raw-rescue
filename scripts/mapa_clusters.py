#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mapa_clusters.py - mapa de una IMAGEN de tarjeta cluster por cluster (solo lectura).

Para cada cluster calcula:
  * tipo: ZERO (todo ceros), RAW (tira ARW2 de Sony), JPEG, TIFF (cabecera de RAW) u OTRO
  * costo de empalme entre el cluster i y el i+1: diferencia media entre los ultimos LAG bloques del cluster i y los
    primeros LAG bloques del i+1 (bloques de 16 bytes con max/min de 11 bits en su cabecera). Si i e i+1 son vecinos
    reales dentro de una foto el costo es bajo (ruido normal 5-40); si hay un salto (otro archivo, bloque cambiado de
    lugar, volcado de FAT en medio de los datos) sube a cientos.
Imprime: cabeceras TIFF (fecha, tamano segun TIFF, clusters esperados), tramos por tipo, saltos RAW|RAW con costo alto y
el PERIODO con que se repiten los saltos fuertes (= tamano de la unidad en que la camara/tarjeta escribio los datos).

Uso:
  python mapa_clusters.py IMAGEN [--c0 2] [--n 0] [--out mapa.npz] [--lag 1200] [--thr 120] [--strong 400]
    --c0      primer cluster a mapear (numerado como en la FAT; el primero de datos es el 2)
    --n       cuantos clusters (0 = hasta el final de la imagen)
    --lag     bloques comparados en cada empalme = 2 filas de bloques = 2*ancho_fila_bytes/16  (A7R V: 1200)
    --thr     costo minimo para listar un salto;  --strong: costo desde el que un salto cuenta para el periodo
    --out     guarda types/cost/... en un .npz para analisis posteriores (flujo.py, pieces.py, orphans.py)
    --off/--cs  solo si el boot sector no se puede leer: byte del cluster --c0 y bytes por cluster
Los tipos RAW suponen ARW2 de Sony (bloques de 16 bytes). Con otros formatos solo son confiables ZERO/JPEG/TIFF.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

T_ZERO, T_RAW, T_JPEG, T_OTRO, T_TIFF = 0, 1, 2, 3, 4
NAMES = ['ZERO', 'RAW', 'JPEG', 'OTRO', 'TIFF']


def geometria(path):
    """(byte del cluster 2, bytes por cluster) o None si el boot sector no se puede leer."""
    try:
        fs = rs.abrir_volumen(rs.Source(path), 1)                # FAT32 o exFAT (solo geometria)
        return fs.base + fs.data_off, fs.csize
    except (SystemExit, Exception):
        return None


def periodo(strong, minimo=8, pmin=30, pmax=4000):
    """strong: clusters donde empieza un tramo tras un salto fuerte. Mira las DISTANCIAS entre saltos consecutivos y busca
    el P del que son multiplos la mayoria (asi no importa que la grilla se corra despues de un volcado de metadatos).
    Los divisores del periodo real puntuan igual, por eso se elige el P MAS GRANDE con puntaje cercano al maximo.
    Devuelve (P, fraccion_de_intervalos_multiplos_de_P, cantidad_de_intervalos) o None."""
    s = np.unique(np.asarray(strong, dtype=np.int64))
    if len(s) < minimo:
        return None
    gaps = np.diff(s)
    frac = np.zeros(pmax, dtype=np.float64)
    for P in range(pmin, pmax):
        frac[P] = float(np.mean(gaps % P == 0))
    top = frac.max()
    if top < 0.4:
        return None
    P = int(np.nonzero(frac >= 0.9 * top)[0].max())
    return P, float(frac[P]), int(len(gaps))


def main():
    ap = argparse.ArgumentParser(description='Mapa de tipos y empalmes de una imagen de tarjeta (solo lectura).')
    ap.add_argument('imagen')
    ap.add_argument('--c0', type=int, default=2)
    ap.add_argument('--n', type=int, default=0)
    ap.add_argument('--off', type=int, default=None)
    ap.add_argument('--cs', type=int, default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--lag', type=int, default=1200)
    ap.add_argument('--thr', type=float, default=120.0)
    ap.add_argument('--strong', type=float, default=400.0)
    a = ap.parse_args()

    geo = geometria(a.imagen)
    cs = a.cs or (geo[1] if geo else 32768)
    if a.off is not None:
        off = a.off
    elif geo:
        off = geo[0] + (a.c0 - 2) * cs
    else:
        raise SystemExit('No pude leer la geometria FAT32 de la imagen: pasar --off (byte del cluster --c0) y --cs.')
    size = os.path.getsize(a.imagen)
    n = a.n or max(0, (size - off) // cs)
    print('clusters a mapear: %d (desde cluster %d, cluster de %d bytes)' % (n, a.c0, cs))

    types = np.zeros(n, dtype=np.uint8)
    cost = np.zeros(n, dtype=np.float32)       # costo de empalme entre cluster i y i+1
    ratio = np.zeros(n, dtype=np.float32)
    jr = np.zeros(n, dtype=np.float32)
    CH = 1024
    prev_tail = None
    with open(a.imagen, 'rb') as f:
        for s in range(0, n, CH):
            m = min(CH, n - s)
            f.seek(off + s * cs)
            buf = f.read(m * cs)
            arr = np.frombuffer(buf, dtype=np.uint8)
            if len(arr) < m * cs:
                arr = np.concatenate([arr, np.zeros(m * cs - len(arr), dtype=np.uint8)])
            arr = arr.reshape(m, cs)
            w = arr.view('<u4')[:, ::4]                         # primera palabra de cada bloque de 16 bytes
            mx = (w & 0x7FF).astype(np.int16)
            mn = ((w >> 11) & 0x7FF).astype(np.int16)
            rt = (mx >= mn).mean(axis=1)                        # en ARW2 max >= min siempre; en datos cualquiera ~50%
            isff = arr == 0xFF
            nff = isff.sum(axis=1)
            nff00 = (isff[:, :-1] & (arr[:, 1:] == 0)).sum(axis=1)
            jrat = nff00 / np.maximum(nff, 1)                   # en JPEG todo 0xFF va seguido de 0x00 (byte stuffing)
            zero = ~arr.any(axis=1)
            tiff = (arr[:, 0] == 0x49) & (arr[:, 1] == 0x49) & (arr[:, 2] == 0x2A) & (arr[:, 3] == 0)
            t = np.full(m, T_OTRO, dtype=np.uint8)
            t[(nff >= 20) & (jrat >= 0.85)] = T_JPEG
            t[rt >= 0.985] = T_RAW
            t[zero] = T_ZERO
            t[tiff] = T_TIFF
            types[s:s + m] = t
            ratio[s:s + m] = rt
            jr[s:s + m] = jrat
            L = a.lag
            hx, hn = mx[:, :L], mn[:, :L]
            tx, tn = mx[:, -L:], mn[:, -L:]
            c = np.abs(tx[:-1] - hx[1:]).mean(axis=1) + np.abs(tn[:-1] - hn[1:]).mean(axis=1)
            cost[s:s + m - 1] = c
            if prev_tail is not None:
                px, pn = prev_tail
                cost[s - 1] = np.abs(px - hx[0]).mean() + np.abs(pn - hn[0]).mean()
            prev_tail = (tx[-1].copy(), tn[-1].copy())

        # cabeceras TIFF
        hdrs = []
        for i in np.where(types == T_TIFF)[0]:
            f.seek(off + int(i) * cs)
            d = f.read(1 << 20)
            t = rs.Tiff(d)
            if not t.ok:
                continue
            info = t.info()
            i0 = t.ifd(t.ifd0)
            so = sc = W = H = 0
            for o in (t.ints(i0[0x14A]) if 0x14A in i0 else []):
                ifd = t.ifd(o)
                if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767] and 0x111 in ifd:
                    so = t.ints(ifd[0x111])[0]
                    sc = t.ints(ifd[0x117])[0]
                    W = t.ints(ifd[0x100])[0]
                    H = t.ints(ifd[0x101])[0]
            model = t.text(i0[0x110]) if 0x110 in i0 else ''
            hdrs.append(dict(i=int(i), date=info['date'], need=info['need'], so=so, sc=sc, W=W, H=H, model=model))

    print('cabeceras TIFF: %d' % len(hdrs))
    for h in hdrs:
        print('  cluster %7d  %s  %-10s tam=%9d (%d clusters)  tira@%d  %dx%d' % (
            a.c0 + h['i'], h['date'], h['model'], h['need'], (h['need'] + cs - 1) // cs, h['so'], h['W'], h['H']))

    print('mapa de tipos (tramos >= 3 clusters, o cabeceras):')
    i = 0
    while i < n:
        j = i
        while j + 1 < n and types[j + 1] == types[i]:
            j += 1
        if j - i + 1 >= 3 or types[i] == T_TIFF:
            print('  %-5s %7d..%-7d (%d)' % (NAMES[types[i]], a.c0 + i, a.c0 + j, j - i + 1))
        i = j + 1

    big = [(i, float(cost[i])) for i in range(n - 1) if types[i] == T_RAW and types[i + 1] == T_RAW and cost[i] > a.thr]
    print('saltos RAW|RAW con costo > %.0f: %d' % (a.thr, len(big)))
    for i, c in big[:200]:
        print('  %7d|%-7d  costo=%.0f' % (a.c0 + i, a.c0 + i + 1, c))
    if len(big) > 200:
        print('  ... y %d mas (estan en el .npz si se uso --out)' % (len(big) - 200))

    fuertes = [a.c0 + i + 1 for i in range(n - 1) if types[i] == T_RAW and types[i + 1] == T_RAW and cost[i] > a.strong]
    p = periodo(fuertes)
    print('saltos fuertes (costo > %.0f): %d' % (a.strong, len(fuertes)))
    if p:
        print('PERIODO de los saltos: %d clusters (%.0f%% de los %d intervalos entre saltos fuertes son multiplos de %d)  -> posible unidad de escritura'
              % (p[0], 100 * p[1], p[2], p[0]))
    else:
        print('sin periodo claro en los saltos (hacen falta >= 8 saltos fuertes y que >= 40% de los intervalos sean multiplos de un mismo P)')

    if a.out:
        np.savez_compressed(a.out, types=types, cost=cost, ratio=ratio, jr=jr, c0=a.c0, off=off,
                            hdr=np.array([(h['i'], h['need'], h['so'], h['sc'], h['W'], h['H']) for h in hdrs], dtype=np.int64))
        print('guardado', a.out)


if __name__ == '__main__':
    main()
