#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carve_cabeceras.py - busca cabeceras de RAW (TIFF: ARW, NEF, CR2, DNG...) alineadas a cluster en una IMAGEN de tarjeta y
saca cada archivo asumiendo que es CONTIGUO desde su cabecera. Solo lee la imagen.

Sirve para lo que la FAT ya no sabe: fotos borradas, archivos cuyo directorio se perdio, tarjetas formateadas, ultimas
fotos de una tarjeta. El tamano sale de la propia estructura TIFF (fin de la ultima tira / del JPEG) mas el relleno final
que deja cada marca: ARW de Sony A7R V = 1024 bytes de ceros; NEF/CR2 = 0.

OJO: si la foto estaba fragmentada, el archivo sale con datos ajenos. Por eso cada cabecera se lista con su estado en la
FAT y conviene verificar lo extraido (--verificar abre con LibRaw; mapa_clusters.py muestra saltos de empalme; ubicar_filas.py
y verificar_filas.py comparan contra el JPEG embebido).

Estados (columna 'estado'):
  LIBRE            la FAT no tiene el cluster asignado: foto borrada (o FAT perdida)
  VIVO_OK          cadena FAT completa y del largo esperado: rescatar_sd.py extract ya la saca igual
  VIVO_DISTINTO    hay cadena pero con otro largo o fragmentada (la FAT y la estructura TIFF no coinciden)
  EN_CADENA        el cluster pertenece a la cadena de otro archivo (cabecera dentro de otro archivo)
  SIN_FAT          volumen exFAT (tarjetas de 64 GB o mas): solo se conoce la geometria, no hay FAT ni directorio que
                   consultar; se extraen como contiguas. Probado solo con una imagen exFAT sintetica.

Uso:
  python carve_cabeceras.py IMAGEN                       lista las cabeceras (no escribe nada)
  python carve_cabeceras.py IMAGEN --out DIR             ademas extrae las de estado LIBRE
  python carve_cabeceras.py IMAGEN --out DIR --todas     extrae todas
  python carve_cabeceras.py IMAGEN --out DIR --heads 972729,973540   solo esos clusters
Opciones: --min-mb 3 (ignora TIFF mas chicos), --fat 1|2, --verificar (decodifica con rawpy cada archivo extraido).
"""
import argparse
import csv
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

EXT = {'ARW': 'ARW', 'NEF': 'NEF', 'CR2': 'CR2', 'TIFF': 'TIF'}
MARCA = {'ARW': 'SONY', 'NEF': 'NIKON', 'CR2': 'CANON', 'TIFF': 'RAW'}
RELLENO = {'ARW': 1024}                      # bytes de ceros despues del ultimo dato (A7R V); 0 en el resto


def cabeceras(imagen, fs):
    """Clusters (numerados como en la FAT) cuyo primer sector empieza con firma TIFF. Lee 1 byte por cluster."""
    mm = np.memmap(imagen, dtype=np.uint8, mode='r')
    base = fs.base + fs.data_off
    n = min(fs.ncl, max(0, (len(mm) - base) // fs.csize))
    if n <= 0:
        raise SystemExit('La imagen no tiene area de datos legible.')
    col0 = mm[base: base + n * fs.csize: fs.csize]
    cand = np.nonzero((col0 == 0x49) | (col0 == 0x4D))[0]
    out = []
    for k in cand:
        p = base + int(k) * fs.csize
        if bytes(mm[p:p + 4]) in (b'II*\x00', b'MM\x00*'):
            out.append(int(k) + 2)
    del mm
    return out


def main():
    ap = argparse.ArgumentParser(description='Busca y extrae RAW por cabecera TIFF alineada a cluster (solo lectura).')
    ap.add_argument('imagen')
    ap.add_argument('--out')
    ap.add_argument('--todas', action='store_true')
    ap.add_argument('--heads')
    ap.add_argument('--min-mb', type=float, default=3.0)
    ap.add_argument('--fat', type=int, default=1)
    ap.add_argument('--verificar', action='store_true')
    a = ap.parse_args()

    fs = rs.abrir_volumen(rs.Source(a.imagen), a.fat)             # FAT32; en exFAT solo geometria (estado SIN_FAT)
    t0 = time.time()
    heads_fat = set(fs.find_heads()[0]) if fs.fat is not None else set()
    cl = cabeceras(a.imagen, fs)
    print('cabeceras TIFF alineadas a cluster: %d  (%.0fs)' % (len(cl), time.time() - t0))
    sel_manual = set(int(x) for x in a.heads.split(',')) if a.heads else None

    filas = []
    for c in cl:
        head = fs.rd(fs.coff(c), min(1 << 20, max(fs.csize, (fs.lim - c) * fs.csize)))
        t = rs.Tiff(head)
        if not t.ok:
            continue
        info = t.info()
        need = info['need']
        if need < a.min_mb * 1e6 and not (sel_manual and c in sel_manual):
            continue
        sig = rs.classify(head[:8192])
        i0 = t.ifd(t.ifd0)
        modelo = t.text(i0[0x110]) if 0x110 in i0 else ''
        size = need + RELLENO.get(sig, 0)
        ncl = -(-size // fs.csize)
        if fs.fat is None:
            estado = 'SIN_FAT'
        elif (fs.fat[c] & rs.MASK) == 0:
            estado = 'LIBRE'
        elif c in heads_fat:
            n, frags, fin = fs.stats(c)
            estado = 'VIVO_OK' if (fin == 'EOC' and n == ncl) else 'VIVO_DISTINTO(%d cl, %d frag, %s)' % (n, frags, fin)
        else:
            estado = 'EN_CADENA'
        filas.append(dict(cluster=c, tipo=sig, modelo=modelo, fecha=info['date'], bytes_esperados=size, clusters=ncl, estado=estado))

    print('  cluster  tipo  modelo       fecha                bytes_esperados  clusters  estado')
    for r in filas:
        print('  %7d  %-4s  %-11s  %-19s  %14d  %8d  %s' % (r['cluster'], r['tipo'], r['modelo'][:11], r['fecha'], r['bytes_esperados'], r['clusters'], r['estado']))
    por_estado = {}
    for r in filas:
        k = r['estado'].split('(')[0]
        por_estado[k] = por_estado.get(k, 0) + 1
    print('resumen:', por_estado)
    if not a.out:
        print('(no se extrajo nada: usar --out DIR para extraer las LIBRES, o --todas)')
        return

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'carve_lista.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()) if filas else ['cluster'])
        w.writeheader()
        w.writerows(filas)
    usados, reporte = {}, []
    for r in filas:
        c = r['cluster']
        if sel_manual is not None:
            if c not in sel_manual:
                continue
        elif not a.todas and r['estado'] not in ('LIBRE', 'SIN_FAT'):
            continue
        disp = min(r['clusters'], fs.lim - c)                  # clusters que realmente existen en la tarjeta
        d = fs.read_clusters(list(range(c, c + disp)))[:r['bytes_esperados']]
        trunc = len(d) < r['bytes_esperados']
        marca = MARCA.get(r['tipo'], 'RAW')
        fe = r['fecha']
        base = '%s_%s' % (marca, fe.replace(':', '').replace(' ', '_')) if fe else '%s_c%d' % (marca, c)
        k = usados.get(base, 0)
        usados[base] = k + 1
        nombre = base + ('' if k == 0 else '_' + 'bcdefghijklmnopqrstuvwxyz'[k - 1]) + '.' + EXT.get(r['tipo'], 'TIF')
        ruta = os.path.join(a.out, nombre)
        with open(ruta, 'wb') as g:
            g.write(d)
        try:
            ts = time.mktime(time.strptime(fe, '%Y:%m:%d %H:%M:%S'))
            os.utime(ruta, (ts, ts))
        except Exception:
            pass
        item = dict(archivo=nombre, cluster=c, estado=r['estado'], bytes_esperados=r['bytes_esperados'], bytes_en_tarjeta=len(d),
                    truncado=bool(trunc), fecha=fe)
        if a.verificar:
            try:
                import rawpy
                with rawpy.imread(ruta) as raw:
                    raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
                item['decodifica'] = True
            except Exception as ex:
                item['decodifica'] = False
                item['error'] = str(ex)[:120]
        reporte.append(item)
        print('  extraido %-34s cluster %-7d %11d bytes%s%s' % (
            nombre, c, len(d), '  TRUNCADO (esperado %d)' % r['bytes_esperados'] if trunc else '',
            ('  decodifica=%s' % item['decodifica']) if a.verificar else ''))
    with open(os.path.join(a.out, 'reporte_carve.json'), 'w', encoding='utf-8') as g:
        json.dump(reporte, g, indent=1, ensure_ascii=False)
    print('LISTO: %d archivos en %s (reporte_carve.json, carve_lista.csv)' % (len(reporte), a.out))


if __name__ == '__main__':
    main()
