import sys

import numpy as np

IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
U = 383
LAG = 1200
O = 924673
NU = 38
f = open(IMG, 'rb')


def clu(c, n=1):
    f.seek(DATA + (c - 2) * CS)
    return f.read(CS * n)


def feats(b):
    w = np.frombuffer(b, dtype='<u4')[::4]
    return (w & 0x7FF).astype(np.int16), ((w >> 11) & 0x7FF).astype(np.int16)


def phys_unit_start(k):
    return O + U * (k ^ 1)


dir_starts = {925821: 'DSC01431', 927859: 'DSC01432', 929891: 'DSC01433', 931897: 'DSC01434', 933932: 'DSC01435',
              935966: 'DSC01436', 937902: 'DSC01437'}
print('unidad logica -> fisica | costo de empalme con la siguiente (logica)')
prev_tail = None
for k in range(NU):
    ps = phys_unit_start(k)
    lstart = O + U * k
    b = clu(ps, U)
    hx, hn = feats(b[:CS])
    tx, tn = feats(b[-CS:])
    c = None
    if prev_tail is not None:
        c = float(np.abs(prev_tail[0] - hx[:LAG]).mean() + np.abs(prev_tail[1] - hn[:LAG]).mean())
    marks = [dir_starts[x] for x in dir_starts if lstart <= x < lstart + U]
    print('  L%02d [%d..%d)  <- fisica %d   costo_desde_anterior=%s  %s' % (
        k, lstart, lstart + U, ps, ('%.0f' % c) if c is not None else '-', ' '.join('(%s en +%d)' % (m, x - lstart) for m, x in
                                                                              [(dir_starts[x], x) for x in dir_starts if lstart <= x < lstart + U])))
    prev_tail = (tx[-LAG:], tn[-LAG:])
