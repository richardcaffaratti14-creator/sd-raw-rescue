"""Escribe LEEME.txt y SHA256.txt de la carpeta final.   (venv)"""
import hashlib
import json
import os
import sys

BASE = r'D:\RESCATE_SD'
FINAL = os.path.join(BASE, 'RECUPERADO_FINAL')
rep = json.load(open(os.path.join(BASE, 'logs', 'reporte_final.json'), encoding='utf-8'))
arw = [r for r in rep if r['archivo'].startswith('DSC')]
nef = [r for r in rep if r['archivo'].startswith('D7200')]
c = lambda lst, e: [r for r in lst if r['estado'] == e]
completas, reparadas, parciales = c(arw, 'COMPLETA'), c(arw, 'REPARADA'), c(arw, 'PARCIAL')
gen = [r['archivo'][:-4] for r in arw if r['jpg'].startswith('generado')]
nm = lambda lst: ', '.join(r['archivo'][3:-4] if r['archivo'].startswith('DSC') else r['archivo'] for r in lst)
txt = f"""RECUPERACION DE LA TARJETA SD - "<NOMBRE DEL EVENTO>" (<FECHA>, <CAMARA>)
===================================================================================

QUE HAY EN ESTA CARPETA
  RAW_Sony_ARW\\             {len(arw)} fotos RAW: DSC01396 a DSC01453. Son TODAS las que quedaron en la tarjeta.
  JPG_camara\\               el JPG de cada una (el que la camara guardo dentro del RAW; ver nota de abajo).
  RAW_Nikon_NEF_2021\\       {len(nef)} RAW viejos de una Nikon D7200 (nov-2021) que tambien estaban en la tarjeta, con su JPG.
  EXTRA_foto_vieja_parcial\\ resto de una foto vieja borrada (no es del evento).
  HOJA_DE_CONTACTO_*.jpg    miniaturas de las 58 fotos, para mirarlas de un vistazo.
  REPORTE.csv               estado de cada archivo, una linea por foto (abrir con Excel).
  SHA256.txt                huellas digitales de todos los archivos (para comprobar copias).
  herramienta\\              programa para repetir el rescate en otra tarjeta.

ESTADO DE LAS FOTOS (detalle en REPORTE.csv)
  COMPLETAS ({len(completas)}): recuperadas enteras, byte a byte, sin ningun retoque: {nm(completas)}
  REPARADAS ({len(reparadas)}): a la tarjeta le faltaban pedazos y se completaron: {nm(reparadas)}
  PARCIAL   ({len(parciales)}): {nm(parciales)}

LO QUE TENES QUE SABER DE LAS REPARADAS
  * Los pedazos que la tarjeta no guardo se rehicieron desde el JPEG que la camara metio dentro de cada RAW. Esas filas tienen
    calidad de JPEG (8 bits, ya con el revelado de la camara), no de RAW real. El resto de la imagen es RAW original.
  * Fotos con mas relleno: DSC01430 (36% de la imagen, la parte de arriba), DSC01453 (18%, abajo; la camara se apago y la
    tarjeta solo tiene 1604 de sus 1938 clusters), DSC01446 (11%, arriba), DSC01445 (9.5%, abajo). Las demas tienen menos del 3%.
  * DSC01438 y DSC01441: se les rearmo la cabecera a partir de la foto vecina; la imagen RAW esta entera.
  * DSC01400: la tarjeta nunca grabo su principio. Esta el 88% inferior; el 12% de arriba es negro. DSC01401 es casi el mismo
    cuadro (misma rafaga) por si necesitas esa parte.
  * Nombres: DSC01400 a DSC01403 y DSC01446 a DSC01453 no figuraban en el directorio; se nombraron por el orden de grabacion.
    Los de la Nikon se nombraron por la hora de disparo (el directorio de esa epoca no existe).
  * JPG generados desde el RAW (el de la camara estaba roto o no existe): {', '.join(gen)}. Se ven distinto al de la camara
    (revelado basico, sin el estilo de imagen de Sony).
  * NEF D7200_20211129_095959: la tarjeta termina antes de su final (72%). El RAW no abre, pero su JPG de 6000x4000 esta entero.

QUE LE PASO A LA TARJETA (resumen)
  Las fotos quedaron grabadas en bloques de 12,5 MB con los bloques de a pares intercambiados, y en medio de las fotos se mezclaron
  copias de la tabla de archivos (FAT) y del directorio. Fue una falla de escritura (de la tarjeta o de la camara: no se puede saber
  cual). Por eso Windows mostraba basura y PhotoRec mezclaba pedazos (las bandas magenta). Se leyo la tarjeta entera en solo
  lectura (nunca se escribio), se reconstruyo el directorio real desde esas copias y se rearmo el orden de los bloques.
  Conviene no usar mas esta tarjeta para trabajo.

NO INCLUIDO
  Quedan en la tarjeta unos 26 GB de video AVCHD (los .m2ts que PhotoRec dejo en D:\\recuperado). No se tocaron.
"""
open(os.path.join(FINAL, 'LEEME.txt'), 'w', encoding='utf-8').write(txt)
print(txt)
# SHA256 de todo
out = []
for root, _, fns in os.walk(FINAL):
    for fn in sorted(fns):
        if fn in ('SHA256.txt',):
            continue
        p = os.path.join(root, fn)
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for blk in iter(lambda: f.read(8 << 20), b''):
                h.update(blk)
        out.append('%s *%s' % (h.hexdigest(), os.path.relpath(p, FINAL)))
open(os.path.join(FINAL, 'SHA256.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('SHA256:', len(out), 'archivos')
