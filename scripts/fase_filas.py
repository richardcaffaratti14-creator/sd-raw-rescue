#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fase_filas.py - en que byte empiezan las filas de una tira ARW2 (Sony) cuando no se sabe donde esta la cabecera.

Para una foto a la que le faltan la cabecera o los primeros clusters no se conoce 'so' (offset de la tira RAW dentro del
archivo), asi que tampoco donde empieza cada fila de 9600 bytes. Idea: cada tramo de 32 columnas son dos bloques de
16 bytes; el bloque par guarda las columnas pares y el impar las impares. En un sensor RGGB la diferencia (impar - par)
cambia de signo en cada fila; con la fase correcta el signo es constante a lo largo de los 300 tramos de una fila.
Probando las 300 fases posibles (paso de 32 bytes) gana la que mas coherencia de signo tiene.

Uso:
  python fase_filas.py IMAGEN CLUSTER_INICIO N_CLUSTERS [--so SO --k0 K0]
    IMAGEN           imagen de la tarjeta (archivo)
    CLUSTER_INICIO   primer cluster (numerado como en la FAT) del trozo de tira a analizar
    N_CLUSTERS       cuantos clusters contiguos mirar (unos 200 alcanzan; menos de 10 filas completas no es confiable)
    --so / --k0      si ya se sospecha 'so' y en que cluster del archivo (k0) empieza el trozo, se imprime la fase
                     esperada ((so - k0*cluster) mod 9600) para compararla con la medida.
Salida: 'fase' = bytes desde el inicio del trozo hasta el primer comienzo de fila (mod 9600), con 32 bytes de precision.
Con eso: so = k0*cluster - (fase_en_el_trozo ...) -> ver references/arw2-sony.md ("fase de filas").
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

W = 9600                 # bytes por fila de la A7R V (1 byte por pixel)
CS_DEF, DATA_DEF = 32768, 8388608


def signo_d(buf):
    """buf: bytes de la tira (alineado a 16). Devuelve D por tramo de 32 columnas = (promedio impar) - (promedio par)."""
    a = np.frombuffer(buf, dtype=np.uint8)
    n = len(a) // 32
    w = a[:n * 32].reshape(n, 2, 16)[:, :, :4].copy().view('<u4')[:, :, 0].astype(np.int64)
    avg = ((w & 0x7FF) + ((w >> 11) & 0x7FF)) / 2.0
    return avg[:, 1] - avg[:, 0]


def mejor_fase(buf):
    """Devuelve (offset_bytes, mejor, segundo). Las filas empiezan en offset (mod 9600) contado desde el inicio de buf.
    mejor/segundo = (puntaje, coherencia, alternancia, offset)."""
    S = np.sign(signo_d(buf))
    out = []
    for p in range(300):
        R = (len(S) - p) // 300
        if R < 4:
            continue
        rows = S[p:p + 300 * R].reshape(R, 300)
        frac = np.abs(rows.mean(axis=1)).mean()               # coherencia de signo dentro de cada fila
        sg = np.sign(rows.mean(axis=1))
        alt = (sg[1:] != sg[:-1]).mean()                      # el signo debe alternar entre filas
        out.append((frac * (0.5 + 0.5 * alt), frac, alt, p * 32))
    out.sort(reverse=True)
    if len(out) < 2:
        raise SystemExit('Trozo demasiado corto: hacen falta al menos ~10 filas (100 KB).')
    mejor = out[0]
    # el segundo puesto de verdad excluye las fases vecinas (+-32 bytes), que siempre puntuan parecido
    otros = [o for o in out[1:] if min((o[3] - mejor[3]) % W, (mejor[3] - o[3]) % W) > 32]
    return mejor[3], mejor, (otros[0] if otros else out[1])


def geometria(imagen):
    """(byte del cluster 2, bytes por cluster) leidos del boot sector; si falla, los de una SD de 32 GB."""
    try:
        import rescatar_sd as rs
        fs = rs.abrir_volumen(rs.Source(imagen), 1)
        return fs.base + fs.data_off, fs.csize
    except (SystemExit, Exception):
        return DATA_DEF, CS_DEF


def main():
    ap = argparse.ArgumentParser(description='Fase de filas de una tira ARW2 sin cabecera.')
    ap.add_argument('imagen')
    ap.add_argument('cluster', type=int)
    ap.add_argument('n', type=int)
    ap.add_argument('--so', type=int)
    ap.add_argument('--k0', type=int)
    a = ap.parse_args()
    data0, cs = geometria(a.imagen)
    with open(a.imagen, 'rb') as f:
        f.seek(data0 + (a.cluster - 2) * cs)
        buf = f.read(a.n * cs)
    off, mejor, segundo = mejor_fase(buf)
    print('fase (bytes desde el inicio del trozo): %d   puntaje %.3f  coherencia %.3f  alternancia %.3f' % ((off,) + mejor[:3]))
    print('mejor fase no vecina: %d  (puntaje %.3f)  -> si puntua casi igual que la primera, el trozo es muy corto o no es ARW2' % (segundo[3], segundo[0]))
    if a.so is not None and a.k0 is not None:
        esperado = (a.so - a.k0 * cs) % W
        print('esperado con so=%d y k0=%d: %d   (diferencia %+d bytes; menos de 32 = coincide)' % (
            a.so, a.k0, esperado, ((off - esperado + W // 2) % W) - W // 2))


if __name__ == '__main__':
    main()
