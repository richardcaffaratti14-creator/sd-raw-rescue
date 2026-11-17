"""Arma la carpeta final: RAW (ARW + NEF), JPEG de la camara (o generado del RAW si el de la camara esta roto),
hojas de contacto, reporte CSV y SHA256.   (venv)   Uso: armar_final.py [--sin-jpg]
"""
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
import traceback

import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rescatar_sd as rs

Image.MAX_IMAGE_PIXELS = None
BASE = r'D:\RESCATE_SD'
FINAL = os.path.join(BASE, 'RECUPERADO_FINAL')
D_ARW = os.path.join(FINAL, 'RAW_Sony_ARW')
D_JPG = os.path.join(FINAL, 'JPG_camara')
D_NEF = os.path.join(FINAL, 'RAW_Nikon_NEF_2021')
D_EXT = os.path.join(FINAL, 'EXTRA_foto_vieja_parcial')
for d in (FINAL, D_ARW, D_JPG, D_NEF, D_EXT):
    os.makedirs(d, exist_ok=True)
LOG = None


def say(*a):
    global LOG
    if LOG is None:
        LOG = open(os.path.join(BASE, 'logs', 'armar_final.txt'), 'a', encoding='utf-8')
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + '\n')
    LOG.flush()


# ---------------------------------------------------------------- estado de cada foto
NOTAS = {
    400: ('PARCIAL', 'La tarjeta nunca grabo el principio de este archivo (cabecera y 12% superior de la imagen). Se conserva el 88% inferior; arriba quedan filas negras. Cabecera armada con la de DSC01401 (mismo disparo en rafaga). Fecha estimada.'),
    401: ('COMPLETA', 'Borrada del directorio; recuperada entera desde la tarjeta. Nombre deducido por la numeracion.'),
    402: ('COMPLETA', 'Borrada del directorio; recuperada entera desde la tarjeta. Nombre deducido por la numeracion.'),
    403: ('COMPLETA', 'Borrada del directorio; recuperada entera desde la tarjeta. Nombre deducido por la numeracion.'),
    430: ('REPARADA', 'La tarjeta perdio 674 clusters de este archivo (36% de la imagen, filas 157-2459). Esas filas se rehicieron desde el JPEG de la propia camara: tienen calidad JPEG, no RAW real.'),
    432: ('REPARADA', 'Se rehicieron 9 filas (5901-5910) desde el JPEG de la camara.'),
    437: ('REPARADA', 'Hueco de 57 clusters: 196 filas (5066-5262, 3%) rehechas desde el JPEG de la camara.'),
    438: ('REPARADA', 'Cabecera reconstruida con la de DSC01440; filas sueltas de empalme corregidas.'),
    439: ('REPARADA', 'Filas sueltas de empalme corregidas.'),
    440: ('REPARADA', 'Hueco de 47 clusters: 162 filas (5068-5230, 2.5%) rehechas desde el JPEG de la camara; filas sueltas de empalme corregidas.'),
    441: ('REPARADA', 'Cabecera reconstruida con la de DSC01442; filas sueltas de empalme corregidas.'),
    442: ('REPARADA', 'Filas sueltas de empalme corregidas.'),
    443: ('REPARADA', 'Filas sueltas de empalme corregidas.'),
    444: ('REPARADA', 'Filas sueltas de empalme corregidas.'),
    445: ('REPARADA', 'Se perdio el final (9.5%): filas 5771-6376 rehechas desde el JPEG de la camara.'),
    446: ('REPARADA', 'Se perdio el principio (11%, filas 0-693) y 22 filas (2647-2669): rehechas desde el JPEG de la camara.'),
    453: ('REPARADA', 'Foto cortada en la tarjeta (la camara se apago): solo existian 1604 de 1938 clusters. Filas 5235-6376 (18%) rehechas desde el JPEG de la camara.'),
}
FILAS_SINT = {400: 758, 430: 2302, 432: 9, 437: 196, 440: 162, 445: 605, 446: 715, 453: 1141}
NOMBRE_DEDUCIDO = set(range(446, 454)) | {400, 401, 402, 403}          # claves = numero de foto - 1000
FECHA_ESTIMADA = {400}

ORIGEN = {}
for n in range(1396, 1454):
    nm = 'DSC0%d' % n
    for carpeta in ('BORRADOS', 'REPARADOS', 'RAW_RECUPERADOS', 'REARMADOS'):
        p = os.path.join(BASE, carpeta, nm + '.ARW')
        if os.path.exists(p):
            ORIGEN[n] = (carpeta, p)
            break
assert len(ORIGEN) == 58, sorted(set(range(1396, 1454)) - set(ORIGEN))


def exif_date(d):
    t = rs.Tiff(d[:300000])
    return t.info().get('date', '') if t.ok else ''


def set_mtime(p, fecha):
    try:
        ts = time.mktime(time.strptime(fecha, '%Y:%m:%d %H:%M:%S'))
        os.utime(p, (ts, ts))
    except Exception:
        pass


# ---------------------------------------------------------------- verificacion del JPEG incrustado contra el RAW
def gray_small(rgb, ny=64, nx=96):
    y = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    hh, ww = (y.shape[0] // ny) * ny, (y.shape[1] // nx) * nx
    return y[:hh, :ww].reshape(ny, hh // ny, nx, ww // nx).mean(axis=(1, 3))


def jpeg_gray(jb):
    im = Image.open(io.BytesIO(jb))
    im.draft('RGB', (im.width // 8, im.height // 8))
    return gray_small(np.asarray(im.convert('RGB')).astype(np.float32))


def comparar(g_raw, g_jpg):
    """filas de 'tiras' donde el JPEG no se parece al RAW (tras ajustar una curva monotona)"""
    q = np.quantile(g_raw, np.linspace(0, 1, 25))
    bins = np.clip(np.searchsorted(q, g_raw, side='right') - 1, 0, 23)
    med = np.array([np.median(g_jpg[bins == b]) if (bins == b).any() else np.nan for b in range(24)])
    ok = ~np.isnan(med)
    cen = np.array([np.median(g_raw[bins == b]) if (bins == b).any() else np.nan for b in range(24)])
    mapa = np.interp(g_raw, cen[ok], med[ok])
    res = np.abs(mapa - g_jpg).mean(axis=1)                 # por fila de la grilla (64)
    base = max(np.median(res), 2.0)
    malas = int(((res > 3 * base) & (res > 12)).sum())
    return malas, float(res.max()), float(base)


def main():
    sin_jpg = '--sin-jpg' in sys.argv
    filas = []
    thumbs = []                                   # (nombre, imagen PIL chica) para hojas de contacto
    for n in range(1396, 1454):
        nm = 'DSC0%d' % n
        carpeta, src = ORIGEN[n]
        dst = os.path.join(D_ARW, nm + '.ARW')
        say('==', nm, '<-', carpeta)
        shutil.copyfile(src, dst)
        d = open(dst, 'rb').read()
        fecha = exif_date(d)
        set_mtime(dst, fecha)
        k = n - 1000
        estado, nota = NOTAS.get(k, ('COMPLETA', 'Recuperada entera y verificada (sin filas rehechas).'))
        row = dict(archivo=nm + '.ARW', estado=estado, tam_MB=round(len(d) / 1e6, 1), fecha_camara=fecha + (' (estimada)' if k in FECHA_ESTIMADA else ''),
                   nombre='deducido por la numeracion' if k in NOMBRE_DEDUCIDO else 'original', filas_rehechas=FILAS_SINT.get(k, 0),
                   pct_rehecho=round(100.0 * FILAS_SINT.get(k, 0) / 6376, 1), detalle=nota, origen=carpeta, jpg='')
        if not sin_jpg:
            try:
                t = rs.Tiff(d[:300000])
                i0 = t.ifd(t.ifd0)
                so = 0
                for o in t.ints(i0[0x14A]):
                    ifd = t.ifd(o)
                    if 0x103 in ifd and t.ints(ifd[0x103])[:1] == [32767]:
                        so = t.ints(ifd[0x111])[0]
                with rawpy.imread(dst) as r:
                    rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
                rgb = np.ascontiguousarray(rgb[10:10 + 3168, 16:16 + 4752])      # recorte de la camara (32,20)+9504x6336 a media escala
                g_raw = gray_small(rgb.astype(np.float32))
                th = Image.fromarray(rgb)
                th.thumbnail((360, 240))
                thumbs.append((nm, th))
                jb = None
                for s_, e_, w_, h_ in rs.find_jpegs(d[:so + 4096]):
                    if rs.jpeg_kind(w_, h_) == 'full':
                        cand = d[s_:e_]
                        try:
                            malas, mx, base = comparar(g_raw, jpeg_gray(cand))
                        except Exception as ex:
                            malas, mx, base = 99, 0, 0
                            say('   jpeg no decodifica:', ex)
                        say('   jpeg camara %dx%d  tiras malas=%d  res_max=%.1f base=%.1f' % (w_, h_, malas, mx, base))
                        if malas <= 1:
                            jb = cand
                        break
                outp = os.path.join(D_JPG, nm + '.JPG')
                if jb:
                    open(outp, 'wb').write(jb)
                    row['jpg'] = 'JPEG de la camara'
                else:
                    with rawpy.imread(dst) as r:
                        full = r.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
                    full = np.ascontiguousarray(full[20:20 + 6336, 32:32 + 9504])
                    Image.fromarray(full).save(outp, quality=92)
                    row['jpg'] = 'generado desde el RAW (el de la camara estaba roto o no existe)'
                    say('   JPG generado del RAW', full.shape)
                set_mtime(outp, fecha)
            except Exception:
                say('   ERROR jpg', traceback.format_exc())
                row['jpg'] = 'ERROR'
        filas.append(row)
    # NEF
    nefs = []
    for fn in sorted(os.listdir(os.path.join(BASE, 'NEF_2021'))):
        if fn.lower().endswith('.nef'):
            src = os.path.join(BASE, 'NEF_2021', fn)
            dst = os.path.join(D_NEF, fn)
            shutil.copyfile(src, dst)
            shutil.copystat(src, dst)
            d = open(dst, 'rb').read(300000)
            t = rs.Tiff(d)
            fecha = t.info().get('date', '') if t.ok else ''
            trunc = 'D7200_20211129_095959' in fn
            r_ = dict(archivo=fn, estado='PARCIAL' if trunc else 'COMPLETA', tam_MB=round(os.path.getsize(dst) / 1e6, 1), fecha_camara=fecha,
                      nombre='perdido (el directorio no existe); nombrado por la hora de disparo', filas_rehechas=0, pct_rehecho=0.0,
                      detalle=('La tarjeta termina antes del final del archivo (72% presente): el RAW no abre, pero su JPEG incrustado de 6000x4000 esta entero (ver JPG).' if trunc
                               else 'Recuperada entera (abre en LibRaw).'), origen='NEF_2021', jpg='')
            try:
                jb = None
                for s_, e_, w_, h_ in rs.find_jpegs(open(dst, 'rb').read()):
                    if w_ * h_ >= 20_000_000:
                        jb = open(dst, 'rb').read()[s_:e_]
                if jb:
                    outp = os.path.join(D_NEF, fn[:-4] + '.JPG')
                    open(outp, 'wb').write(jb)
                    set_mtime(outp, fecha)
                    r_['jpg'] = 'JPEG incrustado'
            except Exception:
                say('   ERROR jpg nef', traceback.format_exc())
            nefs.append(r_)
            say('NEF', fn, r_['estado'])
    todo = filas + nefs
    with open(os.path.join(FINAL, 'REPORTE.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(todo[0].keys()), delimiter=';')
        w.writeheader()
        w.writerows(todo)
    json.dump(todo, open(os.path.join(BASE, 'logs', 'reporte_final.json'), 'w'), indent=1, ensure_ascii=False)
    # hojas de contacto (20 por hoja)
    from PIL import ImageDraw
    for p in range(0, len(thumbs), 20):
        chunk = thumbs[p:p + 20]
        sheet = Image.new('RGB', (5 * 366, 4 * 262), (20, 20, 20))
        dr = ImageDraw.Draw(sheet)
        for i, (nm, th) in enumerate(chunk):
            x, y = (i % 5) * 366 + 3, (i // 5) * 262 + 18
            sheet.paste(th, (x, y))
            dr.text((x + 2, y - 15), nm, fill=(255, 255, 0))
        sheet.save(os.path.join(FINAL, 'HOJA_DE_CONTACTO_%d.jpg' % (p // 20 + 1)), quality=88)
    say('FIN armado')


if __name__ == '__main__':
    main()
