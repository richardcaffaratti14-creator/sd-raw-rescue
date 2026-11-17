#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buscar_metadatos.py - busca, DENTRO del area de datos de una IMAGEN, copias sueltas del directorio y de la FAT.

Cuando una escritura falla, la camara o la tarjeta pueden dejar sectores de metadatos (entradas de directorio
'DSC01234ARW' y paginas de la tabla FAT) tirados en medio de los datos de las fotos. Windows/PhotoRec/TestDisk no los
ven porque no estan donde deberian, pero contienen el NOMBRE, el CLUSTER INICIAL, el TAMANO y la FECHA reales de cada
foto y el orden de sus clusters: es lo que permite rearmar los archivos con su nombre original.

Hace dos busquedas sobre el area de datos (o sobre una region):
  1. entradas de directorio 8.3 con extension de foto/video (tambien las borradas, primer byte 0xE5)
  2. sectores con forma de FAT: >=100 de 128 valores de 32 bits son punteros validos (< ultimo cluster) o fin de cadena,
     y >=20 son distintos de cero. Los runs de sectores seguidos suelen ser paginas completas de la FAT.
Lo esperable es ver el directorio legitimo (DCIM/100MSDCF...) en su lugar; lo sospechoso son los hallazgos en medio de
clusters de fotos.

Uso:
  python buscar_metadatos.py IMAGEN [--c0 2] [--n 0] [--sin-dir] [--sin-fat] [--max 40] [--patron REGEX]
    --c0/--n   region a revisar (cluster inicial y cantidad; 0 = hasta el final). Toda la tarjeta de 32 GB tarda
               unos 2-6 minutos; con una region de unos miles de clusters, segundos.
Despues: references/forense.md ("Reconstruir directorio y FAT desde los volcados") y ejemplos_caso/buildfat.py, dumpdir.py.
"""
import argparse
import mmap
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

EXT = b'ARW|NEF|CR2|CR3|JPG|HIF|MP4|MOV|RAF|DNG|MTS|M2T'
# 8 caracteres de nombre: 'DSC01234', 'DSC_1234', 'IMG_1234', 'DSCF1234' ...; el primero puede ser 0xE5 (entrada borrada)
PATRON = (rb'(?:[A-Z_\xe5][A-Z0-9_]{3}[0-9]{4}|[A-Z_\xe5][A-Z0-9_]{2}[0-9]{5})(?:' + EXT + rb')[\x20\x00]')


def fecha_fat(d, t):
    return '%04d-%02d-%02d %02d:%02d:%02d' % (1980 + (d >> 9), (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63, (t & 31) * 2)


def entrada(e):
    nombre = e[:8].decode('latin-1').replace('\xe5', '?')
    ext = e[8:11].decode('latin-1')
    cl = (int.from_bytes(e[20:22], 'little') << 16) | int.from_bytes(e[26:28], 'little')
    tam = int.from_bytes(e[28:32], 'little')
    mod = fecha_fat(int.from_bytes(e[24:26], 'little'), int.from_bytes(e[22:24], 'little'))
    return '%s.%s cluster=%d tam=%d mod=%s' % (nombre, ext, cl, tam, mod)


def buscar_directorio(mm, ini, fin, data0, cs, patron, maxs):
    t0 = time.time()
    pat = re.compile(patron)
    hallazgos = []
    CH = 256 << 20
    for s in range(ini, fin, CH):
        e = min(fin, s + CH + 64)
        blk = mm[s:e]
        for m in pat.finditer(blk):
            hallazgos.append(s + m.start())
    print('== entradas de directorio en la region: %d coincidencias  (%.0fs)' % (len(hallazgos), time.time() - t0))
    sectores = {}
    for p in hallazgos:
        sectores.setdefault(p // 512, []).append(p)
    print('   sectores distintos: %d' % len(sectores))
    for sec in sorted(sectores)[:maxs]:
        rel = sec * 512 - data0
        c, s_en_c = rel // cs + 2, (rel % cs) // 512
        ents = [entrada(bytes(mm[p:p + 32])) for p in sectores[sec]]
        print('   sector %d (cluster %d, sector %d dentro del cluster): %d entradas' % (sec, c, s_en_c, len(ents)))
        for x in ents[:3]:
            print('        ' + x)
    if len(sectores) > maxs:
        print('   ... y %d sectores mas (subir --max)' % (len(sectores) - maxs))


def buscar_fat(mm, base, cs, c0, n, lim, maxs):
    t0 = time.time()
    spc = cs // 512
    runs = []
    CH = 512
    for k, s in enumerate(range(0, n, CH)):
        m = min(CH, n - s)
        off = base + (c0 + s - 2) * cs
        a = np.frombuffer(mm[off: off + m * cs], dtype='<u4')
        if a.size < m * spc * 128:
            break                                              # fin de la imagen
        a = a.reshape(m, spc, 128) & 0x0FFFFFFF
        ok = ((a < lim) | (a >= 0x0FFFFFF8)).sum(axis=2)
        nz = (a != 0).sum(axis=2)
        seq = (a[:, :, 1:] == a[:, :, :-1] + 1).sum(axis=2)       # valores consecutivos = archivo contiguo en la FAT
        for i, j in zip(*np.where((ok >= 100) & (nz >= 20))):
            pos = (c0 + s + int(i)) * spc + int(j)               # numero de sector contado desde el cluster 0
            if runs and pos - runs[-1][1] <= 1:
                runs[-1][1] = pos
                runs[-1][2] += 1
                runs[-1][4] = max(runs[-1][4], int(seq[i, j]))
            else:
                runs.append([pos, pos, 1, int(a[i, j, 0]), int(seq[i, j])])
        if k and k % 200 == 0:
            print('   ... %d/%d clusters revisados' % (s, n), flush=True)
    print('== sectores con forma de FAT: %d runs  (%.0fs)' % (len(runs), time.time() - t0))
    for a0, a1, k, v0, sq in runs[:maxs]:
        extra = ('  FAT PROBABLE: el primer sector seria el de las entradas %d.. (una copia continua; si hay varias copias '
                 'seguidas, separarlas a mano: ver ejemplos_caso/buildfat.py)' % (((v0 - 1) // 128) * 128)) if sq >= 60 \
            else '  dudoso (pocos valores consecutivos)'
        print('   cluster %d sector %d .. cluster %d sector %d  (%d sectores)  primer valor=%d  secuencial max=%d%s' % (
            a0 // spc, a0 % spc, a1 // spc, a1 % spc, k, v0, sq, extra))
    if len(runs) > maxs:
        print('   ... y %d runs mas (subir --max)' % (len(runs) - maxs))


def main():
    ap = argparse.ArgumentParser(description='Busca copias sueltas de directorio y FAT dentro del area de datos (solo lectura).')
    ap.add_argument('imagen')
    ap.add_argument('--c0', type=int, default=2)
    ap.add_argument('--n', type=int, default=0)
    ap.add_argument('--sin-dir', action='store_true')
    ap.add_argument('--sin-fat', action='store_true')
    ap.add_argument('--max', type=int, default=40)
    ap.add_argument('--patron', default=None, help='expresion regular (bytes) alternativa para los nombres de archivo')
    a = ap.parse_args()
    fs = rs.Fat32(rs.Source(a.imagen), 1)
    data0 = fs.base + fs.data_off
    cs = fs.csize
    n = a.n or max(0, min(fs.ncl, (os.path.getsize(a.imagen) - data0) // cs) - (a.c0 - 2))
    ini = data0 + (a.c0 - 2) * cs
    fin = ini + n * cs
    print('imagen %s  cluster de %d KB, datos desde el byte %d, ultimo cluster valido %d' % (a.imagen, cs // 1024, data0, fs.lim - 1))
    print('region: clusters %d..%d (%d)' % (a.c0, a.c0 + n - 1, n))
    with open(a.imagen, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            if not a.sin_dir:
                buscar_directorio(mm, ini, fin, data0, cs, a.patron.encode('latin-1') if a.patron else PATRON, a.max)
            if not a.sin_fat:
                buscar_fat(mm, data0, cs, a.c0, n, fs.lim, a.max)
        finally:
            mm.close()


if __name__ == '__main__':
    main()
