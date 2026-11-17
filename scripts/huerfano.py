"""Foto vieja sin cabecera: arma un ARW con la cola de tira RAW que si esta en la tarjeta + cabecera de una foto vecina.   (venv)
Uso: huerfano.py IMAGEN salida.ARW donante.ARW cluster_fin offset_fin  cluster_ini n_clusters
   (cluster_fin/offset_fin = donde termina la tira RAW; la tira recuperada = clusters cluster_ini .. cluster_ini+n-1, contiguos)
"""
import io
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs
import sintesis as S

CS, W, DATA = 32768, 9600, 8388608       # cluster, bytes por fila, byte del cluster 2 (de ESTA tarjeta: sale de 'rescatar_sd.py scan')
STRIP = 61209600                        # 9600 * 6376 bytes de tira (A7R V)
img_p, out, donor_p = sys.argv[1], sys.argv[2], sys.argv[3]
c_end, off_end, c_ini, n = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
f = open(img_p, 'rb')
f.seek(DATA + (c_ini - 2) * CS)
piece = f.read(n * CS)
# posicion de la pieza dentro de la tira
pos_end_abs = c_end * CS + off_end                      # fin de la tira en coordenadas absolutas
pos_ini_abs = c_ini * CS
t0 = STRIP - (pos_end_abs - pos_ini_abs)                # offset de la pieza dentro de la tira
assert t0 >= 0, t0
assert t0 % 16 == 0, t0
print('pieza en la tira: bytes %d..%d  (filas %.1f .. 6376)' % (t0, STRIP, t0 / W))
donor = open(donor_p, 'rb').read()
td = rs.Tiff(donor[:300000])
i0d = td.ifd(td.ifd0)
prev_off = td.ints(i0d[0x201])[0]
so = 0
for o in td.ints(i0d[0x14A]):
    ifd = td.ifd(o)
    if 0x103 in ifd and td.ints(ifd[0x103])[:1] == [32767]:
        so = td.ints(ifd[0x111])[0]
assert so
new = bytearray(so + STRIP + 1024)
new[0:prev_off] = donor[0:prev_off]
# pieza (recortada al final de la tira)
data = piece[:min(len(piece), STRIP - t0)]
new[so + t0: so + t0 + len(data)] = data
zero = S.encode_rows(np.zeros((1, W), dtype=np.int32))
r_full = -(-t0 // W)
for r in range(0, t0 // W):
    new[so + r * W: so + (r + 1) * W] = zero
rp = t0 // W
if t0 % W:
    cut = t0 - rp * W
    new[so + rp * W: so + rp * W + cut] = new[so + (rp + 2) * W: so + (rp + 2) * W + cut]
open(out, 'wb').write(bytes(new))
print('escrito', out, len(new), ' primera fila completa', r_full)
