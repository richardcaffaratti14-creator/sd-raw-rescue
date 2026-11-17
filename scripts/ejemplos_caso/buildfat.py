import os
import importlib.util

import numpy as np

spec = importlib.util.spec_from_file_location('rs', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rescatar_sd.py'))
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
src = rs.Source(IMG)
fs = rs.Fat32(src, 1)
lim = fs.lim
M = rs.MASK
f = open(IMG, 'rb')


def sec_run(c, s, n):
    f.seek(DATA + (c - 2) * CS + s * 512)
    return np.frombuffer(f.read(n * 512), dtype='<u4').reshape(n, 128) & M


runs = {
    'A1': (939556, 32, 127), 'A2': (939558, 32, 131),
    'B1': (945686, 39, 48), 'B2': (945687, 27, 48),
    'C1': (956625, 56, 47), 'C2': (956626, 44, 47), 'C3': (956627, 39, 33), 'C4': (956628, 11, 33),
}
pages = {}   # base cluster -> list of (run, entries)
for k, (c, s, n) in runs.items():
    a = sec_run(c, s, n)
    v0 = int(a[0, 0])
    base0 = ((v0 - 1) // 128) * 128
    print('%s: %d paginas, primer valor=%d -> base %d..%d' % (k, n, v0, base0, base0 + 128 * n - 1))
    for i in range(n):
        pages.setdefault(base0 + 128 * i, []).append((k, a[i].copy()))

# coherencia entre copias
bad = 0
for b, lst in pages.items():
    for k2, e2 in lst[1:]:
        if not np.array_equal(lst[0][1], e2):
            bad += 1
print('paginas distintas bases: %d, discrepancias entre copias: %d' % (len(pages), bad))

# FAT fusionada: FAT1 valida + paginas volcadas
fat = np.array(fs.fats[0], dtype=np.uint32) & M
fat = fat[:lim].copy()
for b, lst in pages.items():
    e = lst[0][1]
    fat[b:b + 128] = e[:max(0, min(128, lim - b))]
np.save(r'TRABAJO\fat_merged.npy', fat)


def chain(start):
    out, c = [], start
    while 2 <= c < lim and len(out) < 100000:
        out.append(c)
        v = int(fat[c])
        if v >= rs.EOC:
            return out, 'EOC'
        if v == 0:
            return out, 'FREE'
        if v >= lim:
            return out, 'INVALID'
        c = v
    return out, 'BAD'


def frags(cl):
    runs_, s = [], cl[0]
    for i in range(1, len(cl)):
        if cl[i] != cl[i - 1] + 1:
            runs_.append((s, cl[i - 1]))
            s = cl[i]
    runs_.append((s, cl[-1]))
    return runs_


dsc = [(1430, 923785, 66711552), (1431, 925821, 66760704), (1432, 927859, 66555904), (1433, 929891, 65708032),
       (1434, 931897, 66662400), (1435, 933932, 66637824), (1436, 935966, 63438848), (1437, 937902, 66670592),
       (1438, 939937, 66945024), (1439, 941980, 66977792), (1440, 944024, 66945024), (1441, 946067, 67047424),
       (1442, 948114, 67076096), (1443, 950161, 67186688), (1444, 952212, 66686976), (1445, 954248, 65441792)]
print('cadenas con la FAT fusionada:')
for n, c, sz in dsc:
    cl, st = chain(c)
    need = (sz + CS - 1) // CS
    fr = frags(cl)
    print('  DSC0%d  inicio=%d  esperados=%d  cadena=%d  fin=%s  fragmentos=%d  %s' % (
        n, c, need, len(cl), st, len(fr), fr[:4]))
