"""Reconstruye la cabecera (IFD0, MakerNote, SubIFD, miniaturas) de un ARW cuya cabecera quedo pisada por un volcado de FAT,
copiando la de la foto vecina de la rafaga y corrigiendo los campos propios de cada foto.
Uso: rehacer_cabecera.py FOTO.ARW VECINA.ARW SO FECHA SALIDA.ARW
   SO = offset de la tira RAW = tamano - 1024 - 61209600 ;  FECHA = 'AAAA:MM:DD HH:MM:SS' (la de la foto)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rescatar_sd as rs

foto_p, sib_p, so, fecha, path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
D = bytearray(open(foto_p, 'rb').read())
S = open(sib_p, 'rb').read()
ts = rs.Tiff(S[:300000])
i0s = ts.ifd(ts.ifd0)
prev_off = ts.ints(i0s[0x201])[0]
prev_len = ts.ints(i0s[0x202])[0]
# donde empieza el JPEG full propio (primer SOI con 9504x6336 despues de la vista previa)
x = None
pos = prev_off
while True:
    s = D.find(b'\xff\xd8\xff', pos)
    if s < 0 or s > so:
        break
    end, w, h = rs.jpeg_scan(bytes(D[:so + 4096]), s)
    if w == 9504 and h == 6336:
        x = s
        break
    pos = s + 3
print('JPEG full propio en', x, ' vista previa vecina %d bytes en %d' % (prev_len, prev_off))
assert x, 'no encuentro el JPEG full propio'
prev = S[prev_off:prev_off + prev_len]
space = x - prev_off
if prev_len > space:                                   # no entra: se recodifica la vista previa vecina mas liviana
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(prev)).convert('RGB')
    for q in range(90, 40, -4):
        b = io.BytesIO()
        im.save(b, 'JPEG', quality=q)
        if len(b.getvalue()) <= space - 64:
            prev = b.getvalue()
            break
    print('vista previa recodificada a %d bytes (cabian %d)' % (len(prev), space))
new = bytearray(D)
new[0:prev_off] = S[0:prev_off]                       # IFD0, Exif, MakerNote, miniatura, SubIFD
new[prev_off:prev_off + len(prev)] = prev
new[prev_off + len(prev):x] = bytes(x - prev_off - len(prev))
t = rs.Tiff(bytes(new[:300000]))
i0 = t.ifd(t.ifd0)
struct.pack_into('<I', new, i0[0x202][2], len(prev))
# fecha del IFD0
if 0x132 in i0:
    vo = i0[0x132][2]
    new[vo:vo + 20] = (fecha + '\x00').encode('ascii')
# fechas del ExifIFD
ex = t.ifd(t.ints(i0[0x8769])[0])
for tag in (0x9003, 0x9004):
    if tag in ex:
        vo = ex[tag][2]
        new[vo:vo + 20] = (fecha + '\x00').encode('ascii')
# posicion de la tira RAW en el SubIFD
for o in t.ints(i0[0x14A]):
    ifd = t.ifd(o)
    if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767]:
        struct.pack_into('<I', new, ifd[0x111][2], so)
        struct.pack_into('<I', new, ifd[0x117][2], 61209600)
t2 = rs.Tiff(bytes(new[:300000]))
i02 = t2.ifd(t2.ifd0)
for o in t2.ints(i02[0x14A]):
    ifd = t2.ifd(o)
    if 0x103 in ifd and t2.ints(ifd[0x103])[:1] == [32767]:
        print('SubIFD raw:', t2.ints(ifd[0x111]), t2.ints(ifd[0x117]), t2.ints(ifd[0x100]), t2.ints(ifd[0x101]))
open(path, 'wb').write(bytes(new))
print('escrito', path, len(new))
