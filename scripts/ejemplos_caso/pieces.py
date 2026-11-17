import sys

import numpy as np

SP = r'TRABAJO'
IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
LAG = 1200
z = np.load(SP + r'\region.npz')
types, cost, c0 = z['types'], z['cost'], int(z['c0'])
hdr = z['hdr']
R0, R1 = int(sys.argv[1]), int(sys.argv[2])
f = open(IMG, 'rb')


def clu(c):
    f.seek(DATA + (c - 2) * CS)
    return f.read(CS)


def feats(b):
    w = np.frombuffer(b, dtype='<u4')[::4]
    return (w & 0x7FF).astype(np.int16), ((w >> 11) & 0x7FF).astype(np.int16)


T = lambda c: types[c - c0]
C = lambda c: cost[c - c0]          # costo entre c y c+1
RAW, TIFF = 1, 4

# --- limites
strong = [c for c in range(R0 + 1, R1) if T(c) == RAW and T(c - 1) == RAW and C(c - 1) > 400]
res = {}
for c in strong:
    res[c % 383] = res.get(c % 383, 0) + 1
bounds = set()
for c in range(R0 + 1, R1):
    tp, tc = T(c - 1), T(c)
    if tp != tc and (tp == RAW or tc == RAW or tc == TIFF):
        bounds.add(c)
    elif tp == RAW and tc == RAW:
        k = C(c - 1)
        if k > 400:
            bounds.add(c)
        elif k > 100:
            # sobre la reticula local (+-0) de un limite fuerte cercano
            near = [s for s in strong if abs(s - c) <= 4000 and (s - c) % 383 == 0]
            if near:
                bounds.add(c)
bl = sorted(bounds | {R0, R1})
pieces = []
for a, b in zip(bl, bl[1:]):
    tt = np.bincount(types[a - c0:b - c0], minlength=5)
    pieces.append(dict(a=a, b=b, n=b - a, t=int(tt.argmax()), tiff=bool(tt[TIFF])))
print('trozos: %d' % len(pieces))

# --- features de cabeza/cola de cada trozo RAW
for p in pieces:
    if p['t'] == RAW and p['n'] >= 2:
        hx, hn = feats(clu(p['a']))
        tx, tn = feats(clu(p['b'] - 1))
        p['H'] = (hx[:LAG], hn[:LAG])
        p['T'] = (tx[-LAG:], tn[-LAG:])
raws = [p for p in pieces if 'H' in p]
print('trozos RAW: %d' % len(raws))
NAMES = ['ZERO', 'RAW', 'JPEG', 'OTRO', 'TIFF']
print('   idx  desde   hasta     n  tipo   mejores sucesores (idx:costo)')
for i, p in enumerate(pieces):
    line = '%5d %7d %7d %5d %-5s%s' % (i, p['a'], p['b'] - 1, p['n'], NAMES[p['t']], ' H' if p['tiff'] else '  ')
    if 'T' in p:
        cs = []
        for j, q in enumerate(pieces):
            if 'H' in q and q is not p:
                c = float(np.abs(p['T'][0] - q['H'][0]).mean() + np.abs(p['T'][1] - q['H'][1]).mean())
                cs.append((c, j))
        cs.sort()
        line += '  -> ' + '  '.join('%d:%.0f(%d)' % (j, c, pieces[j]['n']) for c, j in cs[:3])
    print(line)
