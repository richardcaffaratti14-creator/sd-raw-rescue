"""Detecta y corrige filas aisladas danadas en los empalmes de unidad (los primeros ~7 KB de cada unidad quedaron pisados).
Una fila es danada si difiere mucho de sus vecinas del mismo color (r-2 y r+2) pero esas vecinas coinciden entre si.
Corrige reemplazandola por el promedio de r-2 y r+2 (re-codificado en bloques ARW2 validos).
Uso (venv): arreglar_empalmes.py archivo.arw so [--solo-informar] [--filas a,b,c]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sintesis as S

W = 9600
CS = 32768


def detectar(d, so, rows):
    """devuelve filas danadas dentro de 'rows'"""
    bad = []
    lo = max(min(rows) - 4, 0)
    hi = min(max(rows) + 5, 6376)
    P = S.decode_rows(d, so, list(range(lo, hi)))
    L = S.L_OF_P[P]
    for r in rows:
        i = r - lo
        if i < 2 or i + 2 >= len(L):
            continue
        a = np.abs(L[i] - L[i - 2]).mean()
        b = np.abs(L[i] - L[i + 2]).mean()
        c = np.abs(L[i - 2] - L[i + 2]).mean()
        if min(a, b) > 3 * max(c, 6) and min(a, b) > 25:
            bad.append(r)
    return bad


def arreglar(buf, so, bad):
    for r in bad:
        P = S.decode_rows(bytes(buf[so + (r - 2) * W: so + (r + 3) * W]), 0, [0, 4])
        avg = ((P[0].astype(np.int64) + P[1].astype(np.int64) + 1) // 2).astype(np.int32)
        # promedio en dominio de codigos (monotono) -> suficiente para una fila aislada
        buf[so + r * W: so + (r + 1) * W] = S.encode_rows(avg.reshape(1, -1))


if __name__ == '__main__':
    path, so = sys.argv[1], int(sys.argv[2])
    solo = '--solo-informar' in sys.argv
    filas = None
    if '--filas' in sys.argv:
        filas = [int(x) for x in sys.argv[sys.argv.index('--filas') + 1].split(',')]
    d = bytearray(open(path, 'rb').read())
    if filas is None:
        raise SystemExit('indicar --filas (filas candidatas, p.ej. las de los empalmes)')
    cand = sorted(set(r for f in filas for r in range(f - 2, f + 3)))
    bad = detectar(bytes(d), so, cand)
    print('%s: candidatas=%d  danadas=%s' % (path.split('\\')[-1], len(cand), bad))
    if bad and not solo:
        arreglar(d, so, bad)
        open(path, 'wb').write(bytes(d))
        print('   corregidas', len(bad), 'filas')
