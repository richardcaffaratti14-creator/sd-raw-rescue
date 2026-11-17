"""DSC01453 (truncada) y DSC01430 (674 clusters que la tarjeta nunca guardo): relleno de filas desde su JPEG.   (venv)"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs
import sintesis as S

CS, W = 32768, 9600
IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
SRC, OUT = r'D:\RESCATE_SD\REARMADOS', r'D:\RESCATE_SD\REPARADOS'
SP = r'TRABAJO'
f = open(IMG, 'rb')


def phys(p, n):
    f.seek(DATA + (p - 2) * CS)
    return f.read(n * CS)


log = {}
# ---------------- DSC01453
base = open(os.path.join(SRC, 'DSC01453.ARW'), 'rb').read()
t = rs.Tiff(base[:300000])
i0 = t.ifd(t.ifd0)
for o in t.ints(i0[0x14A]):
    ifd = t.ifd(o)
    if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767]:
        so, sc = t.ints(ifd[0x111])[0], t.ints(ifd[0x117])[0]
size = so + sc + 1024
r_ok = (len(base) - so) // W
new = bytearray(base) + bytearray(size - len(base))
full = [j for j in rs.find_jpegs(base[:so + 4096]) if rs.jpeg_kind(j[2], j[3]) == 'full'][0]
rep = S.fill_rows(new, so, base[full[0]:full[1]], r_ok - 1, 6376)
open(os.path.join(OUT, 'DSC01453.ARW'), 'wb').write(bytes(new[:size]))
log['DSC01453'] = dict(filas_sinteticas=[[int(r_ok - 1), 6376]], R2=[round(x, 3) for x in rep], **S.fill_rows.info)
print('DSC01453', log['DSC01453'])

# ---------------- DSC01430
so, size = 5500928, 66711552
new = bytearray()
new += phys(923785, 214)            # cabecera + 46 clusters de RAW   k=0..213
new += bytes(674 * CS)              # k=214..887 nunca grabados
new += phys(925056, 383)            # k=888..1270   (L0)
new += phys(924673, 383)            # k=1271..1653  (L1)
new += phys(925822, 382)            # k=1654..2035
assert len(new) == 2036 * CS
new = bytearray(new[:size])
r0 = (214 * CS - so) // W
r1 = -((so - 888 * CS) // W)
rep = S.fill_rows(new, so, open(r'D:\RESCATE_SD\refs2\H923785.jpg', 'rb').read(), r0, r1)
open(os.path.join(OUT, 'DSC01430.ARW'), 'wb').write(bytes(new))
log['DSC01430'] = dict(filas_sinteticas=[[int(r0), int(r1)]], R2=[round(x, 3) for x in rep], **S.fill_rows.info)
print('DSC01430', log['DSC01430'])
p = os.path.join(SP, 'reparaciones.json')
old = json.load(open(p)) if os.path.exists(p) else {}
old.update(log)
json.dump(old, open(p, 'w'), indent=1)
