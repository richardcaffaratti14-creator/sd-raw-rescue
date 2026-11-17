"""Arma una hoja de contacto con las vistas (JPEG) para revisar de un vistazo.
Uso (python del venv):  hoja_contacto.py salida.jpg columnas ancho_miniatura img1 img2 ...
"""
import os
import sys

from PIL import Image, ImageDraw

out, cols, w = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
paths = sys.argv[4:]
thumbs = []
for p in paths:
    im = Image.open(p).convert('RGB')
    h = int(im.height * w / im.width)
    thumbs.append((os.path.splitext(os.path.basename(p))[0].replace('_raw', ''), im.resize((w, h))))
rows = (len(thumbs) + cols - 1) // cols
th = max(t.height for _, t in thumbs) + 18
sheet = Image.new('RGB', (cols * w, rows * th), (20, 20, 20))
d = ImageDraw.Draw(sheet)
for i, (name, t) in enumerate(thumbs):
    x, y = (i % cols) * w, (i // cols) * th
    sheet.paste(t, (x, y + 18))
    d.text((x + 4, y + 3), name, fill=(255, 255, 0))
sheet.save(out, quality=88)
print('hoja guardada:', out, sheet.size)
