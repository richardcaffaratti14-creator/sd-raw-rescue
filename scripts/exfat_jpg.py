#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exfat_jpg.py - rescate de fotos JPG de una tarjeta exFAT (64 GB o mas; la EOS_DIGITAL de Canon y otras). SOLO LECTURA.
Trabaja sobre la IMAGEN de la tarjeta (rescatar_sd.py image E: --out tarjeta.img), nunca sobre la tarjeta.

  python exfat_jpg.py info     tarjeta.img                          geometria, bitmap, arbol de directorios vivo
  python exfat_jpg.py escanear tarjeta.img --trabajo DIR            UNA pasada por toda la imagen: entradas de directorio
                                                                     (vivas, borradas, huerfanas), cabezas JPEG alineadas
                                                                     a cluster y clusters en cero
  python exfat_jpg.py extraer  tarjeta.img --trabajo DIR --out DIR  saca los JPG que conservan su entrada de directorio
  python exfat_jpg.py carvar   tarjeta.img --trabajo DIR --out DIR  saca por cabecera los que quedan sin entrada
  python exfat_jpg.py hojas    CARPETA --out DIR                    hojas de contacto

Por que rinde mas que un carver generico: en exFAT los archivos de una camara quedan en clusters contiguos y un JPG
empieza SIEMPRE al principio de un cluster (se descartan miniaturas y vistas previas embebidas); las entradas de
directorio borradas, o de un directorio viejo que quedo en la zona libre tras un formateo rapido, conservan nombre,
fecha, tamano exacto y cluster inicial, y su suma de verificacion de 16 bits las distingue de la basura; el fin de cada
JPG sale de recorrer sus marcadores (no del primer FFD9, que suele ser el de una miniatura), incluida la vista previa
MPF que Canon pega despues de la imagen.
"""
import argparse
import csv
import io
import json
import os
import re
import struct
import sys
import time
import traceback
from collections import Counter

import numpy as np

LOG = None
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
MARK = re.compile(rb'\xff[^\x00\xd0-\xd7\xff]')          # proximo marcador real despues de datos entropicos
TSIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def say(msg=''):
    msg = str(msg)
    try:
        print(msg, flush=True)
    except Exception:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)
    if LOG:
        LOG.write(msg + '\n')
        LOG.flush()


def human(n):
    return '%.2f GB' % (n / 1e9) if n >= 1e9 else '%.1f MB' % (n / 1e6)


# ---------------------------------------------------------------- volumen exFAT

class Vol:
    """Imagen (o dispositivo) con un volumen exFAT. Solo lectura."""

    def __init__(self, path):
        self.path = path
        try:
            self.f = open(path, 'rb')
        except OSError as ex:
            raise SystemExit('No pude abrir %s: %s' % (path, ex))
        self.size = os.path.getsize(path) if os.path.isfile(path) else None
        self.base = 0
        b = self.rd(0, 512)
        if b[3:11] != b'EXFAT   ' and b[510:512] == b'\x55\xaa':
            for i in range(4):
                ent = b[446 + 16 * i: 462 + 16 * i]
                if ent[4] == 0x07:
                    cand = struct.unpack_from('<I', ent, 8)[0] * 512
                    bb = self.rd(cand, 512)
                    if bb[3:11] == b'EXFAT   ':
                        self.base, b = cand, bb
                        break
        if b[3:11] != b'EXFAT   ':
            raise SystemExit('No es un volumen exFAT (el sector 0 no dice EXFAT).')
        self.boot = b
        self.vol_len = struct.unpack_from('<Q', b, 72)[0]
        fat_off, fat_len, heap_off, self.ncl, self.root = struct.unpack_from('<5I', b, 80)
        self.serial = struct.unpack_from('<I', b, 100)[0]
        self.nfats = b[110]
        self.pct = b[112]
        self.bps = 1 << b[108]
        self.csize = self.bps << b[109]
        if not (9 <= b[108] <= 12) or b[109] > 25 or not self.ncl:
            raise SystemExit('Boot sector exFAT con valores raros (bps shift %d, spc shift %d).' % (b[108], b[109]))
        self.fat_off, self.fat_len, self.heap_off = fat_off * self.bps, fat_len * self.bps, heap_off * self.bps
        self.vol_len *= self.bps
        if self.size and self.base + self.heap_off + self.ncl * self.csize > self.size:
            say('!! La imagen es mas chica que el volumen (%s de %s): lo que falta se lee como ceros.' % (
                human(self.size), human(self.base + self.heap_off + self.ncl * self.csize)))
        raw = self.rd(self.base + self.fat_off, min(self.fat_len, (self.ncl + 2) * 4))
        raw = raw.ljust((self.ncl + 2) * 4, b'\0')[:(self.ncl + 2) * 4]
        self.fat = np.frombuffer(raw, dtype='<u4')

    def rd(self, off, n):
        if n <= 0:
            return b''
        self.f.seek(off)
        return self.f.read(n)

    def coff(self, c):
        return self.base + self.heap_off + (c - 2) * self.csize

    def chain(self, first, limit=1 << 22):
        out, seen, c = [], set(), first
        while 2 <= c < self.ncl + 2 and c not in seen and len(out) < limit:
            out.append(c)
            seen.add(c)
            c = int(self.fat[c])
        return out

    def contig(self, first, nbytes):
        n = max(1, (nbytes + self.csize - 1) // self.csize)
        return list(range(first, min(first + n, self.ncl + 2)))

    def clusters_of(self, s):
        if not s['first']:
            return []
        return self.contig(s['first'], s['dlen']) if s['nofat'] else self.chain(s['first'])

    def entry_clusters(self, s):
        """Clusters de una entrada, en orden. -> (lista, nota). Contiguos si la entrada dice 'sin cadena FAT'; si no, se
        sigue la FAT (la camara fragmenta cuando el espacio libre esta partido, y al borrar NO limpia la cadena). Si la
        cadena no esta entera (largo o fin que no cierran) se supone contiguo y se avisa."""
        n = max(1, (s['dlen'] + self.csize - 1) // self.csize)
        if s['nofat']:
            return self.contig(s['first'], s['dlen']), ''
        ch = self.chain(s['first'], limit=n + 8)
        if len(ch) == n and int(self.fat[ch[-1]]) >= 0xFFFFFFF8:
            tramos = 1 + sum(1 for i in range(1, len(ch)) if ch[i] != ch[i - 1] + 1)
            return ch, ('fragmentada en %d tramos (cadena FAT)' % tramos) if tramos > 1 else ''
        return self.contig(s['first'], s['dlen']), 'CADENA_FAT_INVALIDA: se supuso contiguo'

    def read_list(self, cls, nbytes):
        """Lee los clusters de la lista, juntando los tramos contiguos en una sola lectura."""
        out, i = [], 0
        while i < len(cls):
            j = i
            while j + 1 < len(cls) and cls[j + 1] == cls[j] + 1:
                j += 1
            out.append(self.rd(self.coff(cls[i]), (j - i + 1) * self.csize))
            i = j + 1
        return b''.join(out)[:nbytes]


# ---------------------------------------------------------------- entradas de directorio

def set_checksum(d):
    cs = 0
    for i, v in enumerate(d):
        if i == 2 or i == 3:
            continue
        cs = ((((cs & 1) << 15) | (cs >> 1)) + v) & 0xFFFF
    return cs


def fecha_exfat(ts, inc=0):
    if not ts:
        return None
    s, mi, h = (ts & 31) * 2, (ts >> 5) & 63, (ts >> 11) & 31
    d, mo, y = (ts >> 16) & 31, (ts >> 21) & 15, 1980 + (ts >> 25)
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60):
        return None
    if s + inc // 100 < 60:
        s += inc // 100
    return '%04d-%02d-%02d %02d:%02d:%02d' % (y, mo, d, h, mi, s)


def parse_set(b, off, ncl):
    """Conjunto de entradas de archivo exFAT (File + Stream + Name...) que empieza en b[off]. None si no es valido.
    Vale para entradas vivas (0x85/0xC0/0xC1) y borradas (0x05/0x40/0x41): la suma de verificacion se recalcula
    con el bit 'en uso' restituido, asi que casi no deja pasar basura."""
    n = len(b)
    if off + 96 > n:
        return None
    t0 = b[off]
    if t0 != 0x85 and t0 != 0x05:
        return None
    sc = b[off + 1]
    if sc < 2 or sc > 18:
        return None
    end = off + 32 * (sc + 1)
    if end > n:
        return None
    t1 = b[off + 32]
    if t1 != 0xC0 and t1 != 0x40:
        return None
    nl = b[off + 35]
    nn = (nl + 14) // 15
    if nl == 0 or sc < 1 + nn:
        return None
    for k in range(nn):
        tk = b[off + 64 + 32 * k]
        if tk != 0xC1 and tk != 0x41:
            return None
    d = bytearray(b[off:end])
    for k in range(sc + 1):
        d[32 * k] |= 0x80
    if set_checksum(d) != struct.unpack_from('<H', b, off + 2)[0]:
        return None
    attr = struct.unpack_from('<H', b, off + 4)[0]
    if attr & 0xFFC0:
        return None
    first = struct.unpack_from('<I', b, off + 52)[0]
    dlen = struct.unpack_from('<Q', b, off + 56)[0]
    if (first and not (2 <= first < ncl + 2)) or dlen > ncl * 4 * 1024 * 1024 * 32:
        return None
    raw = b''.join(bytes(b[off + 66 + 32 * k: off + 96 + 32 * k]) for k in range(nn))[:nl * 2]
    ct, mt, at = struct.unpack_from('<III', b, off + 8)
    return dict(name=raw.decode('utf-16-le', 'replace'), attr=attr, first=first, dlen=dlen,
                vdl=struct.unpack_from('<Q', b, off + 40)[0], nofat=bool(b[off + 33] & 2), deleted=(t0 == 0x05),
                creado=fecha_exfat(ct, b[off + 20]), modif=fecha_exfat(mt, b[off + 21]), acceso=fecha_exfat(at),
                nent=sc + 1)


def arbol(vol, maxdirs=20000):
    """Recorre el volumen por la raiz como lo hace el sistema. -> (info, lista de entradas vivas con 'ruta' y 'off')."""
    info = dict(label=None, bitmap=None, upcase=None)
    files, extra = [], []
    root = vol.chain(vol.root)
    info['root_clusters'] = root
    stack, nd = [('', root)], 0
    while stack and nd < maxdirs:
        ruta, cls = stack.pop()
        nd += 1
        data = b''.join(vol.rd(vol.coff(c), vol.csize).ljust(vol.csize, b'\0') for c in cls)
        i, n = 0, len(data) // 32
        while i < n:
            t = data[32 * i]
            if t == 0:
                break
            if not ruta and t == 0x83:
                ln = data[32 * i + 1]
                info['label'] = data[32 * i + 2: 32 * i + 2 + 2 * ln].decode('utf-16-le', 'replace')
            elif not ruta and t == 0x81:
                info['bitmap'] = (struct.unpack_from('<I', data, 32 * i + 20)[0], struct.unpack_from('<Q', data, 32 * i + 24)[0])
            elif not ruta and t == 0x82:
                info['upcase'] = (struct.unpack_from('<I', data, 32 * i + 20)[0], struct.unpack_from('<Q', data, 32 * i + 24)[0])
            elif t == 0x85:
                s = parse_set(data, 32 * i, vol.ncl)
                if s:
                    p = 32 * i
                    s['off'] = vol.coff(cls[p // vol.csize]) + p % vol.csize
                    s['ruta'] = ruta + '/' + s['name']
                    s['viva'] = True
                    files.append(s)
                    if s['attr'] & 0x10 and s['first']:
                        stack.append((s['ruta'], vol.clusters_of(s)))
                    i += s['nent']
                    continue
            i += 1
    info['dirs'] = nd
    return info, files


def mapa_ocupado(vol, info, files):
    """Clusters que reclama algun archivo vivo o el propio sistema de archivos (bitmap, upcase, directorios)."""
    occ = np.zeros(vol.ncl, dtype=bool)

    def mark(cl):
        for c in cl:
            if 2 <= c < vol.ncl + 2:
                occ[c - 2] = True
    mark(info.get('root_clusters', []))
    for key in ('bitmap', 'upcase'):
        if info.get(key):
            f, ln = info[key]
            mark(vol.contig(f, ln))
    for s in files:
        mark(vol.clusters_of(s))
    return occ


def leer_bitmap(vol, info):
    """Bitmap de asignacion: True = cluster marcado como ocupado. None si no se encuentra."""
    if not info.get('bitmap'):
        return None
    first, ln = info['bitmap']
    nbytes = (vol.ncl + 7) // 8
    data = vol.rd(vol.coff(first), min(ln, nbytes)) if ln else b''
    data = data.ljust(nbytes, b'\0')
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder='little')[:vol.ncl].astype(bool)


# ---------------------------------------------------------------- JPEG

def jpeg_walk(d, s=0):
    """Recorre un JPEG que empieza en d[s] marcador por marcador. -> dict(ok, end, w, h, nscan, exif, mpf, motivo).
    Un segundo SOI al nivel de marcadores o un marcador roto significan que el archivo se corto o se mezclo."""
    n = len(d)
    r = dict(ok=False, end=None, w=0, h=0, nscan=0, exif=None, mpf=None, motivo='')
    if n < s + 4 or d[s] != 0xFF or d[s + 1] != 0xD8 or d[s + 2] != 0xFF:
        r['motivo'] = 'sin SOI'
        return r
    i = s + 2
    while True:
        r['pos'] = i
        if i + 2 > n:
            r['motivo'] = 'cortado'
            return r
        if d[i] != 0xFF:
            r['motivo'] = 'marcador roto a los %d bytes' % (i - s)
            return r
        m = d[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD9:
            if r['nscan'] == 0:
                r['motivo'] = 'EOI sin imagen'
                return r
            r['ok'], r['end'] = True, i + 2
            return r
        if m == 0xD8:
            r['motivo'] = 'otro SOI a los %d bytes' % (i - s)
            return r
        if m == 0x00 or m == 0x01 or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            r['motivo'] = 'cortado'
            return r
        ln = (d[i + 2] << 8) | d[i + 3]
        if ln < 2:
            r['motivo'] = 'segmento invalido a los %d bytes' % (i - s)
            return r
        if m in SOF and i + 9 <= n:
            r['h'], r['w'] = (d[i + 5] << 8) | d[i + 6], (d[i + 7] << 8) | d[i + 8]
        elif m == 0xE1 and r['exif'] is None and d[i + 4: i + 10] == b'Exif\0\0':
            r['exif'] = (i + 10, ln - 8)
        elif m == 0xE2 and r['mpf'] is None and d[i + 4: i + 8] == b'MPF\0':
            r['mpf'] = i + 8
        i += 2 + ln
        if m == 0xDA:
            r['nscan'] += 1
            mm = MARK.search(d, i)
            if not mm:
                r['motivo'] = 'cortado dentro de la imagen'
                return r
            i = mm.start()


def mpf_fin(d, r):
    """Canon pega una vista previa grande (formato MPF) despues del EOI. Fin real = fin de la ultima imagen del MPF,
    solo si esa imagen se recorre completa. Devuelve (fin, nota)."""
    fin = r['end']
    b = r['mpf']
    if not b or b + 8 > len(d):
        return fin, ''
    e = '<' if d[b:b + 2] == b'II' else '>' if d[b:b + 2] == b'MM' else None
    if not e:
        return fin, ''
    try:
        ifd = b + struct.unpack_from(e + 'I', d, b + 4)[0]
        cnt = struct.unpack_from(e + 'H', d, ifd)[0]
        for k in range(min(cnt, 20)):
            o = ifd + 2 + 12 * k
            tag, _, c, v = struct.unpack_from(e + 'HHII', d, o)
            if tag != 0xB002 or c < 32:
                continue
            ent = b + v
            for j in range(c // 16):
                _, size, off, _, _ = struct.unpack_from(e + 'IIIHH', d, ent + 16 * j)
                if off == 0:
                    continue
                s2, f2 = b + off, b + off + size
                if f2 <= fin or f2 - r['end'] > (96 << 20):
                    continue
                if f2 > len(d):
                    return fin, 'MPF_FUERA_DE_VENTANA:%d' % f2
                r2 = jpeg_walk(d, s2)
                if r2['ok'] and abs(r2['end'] - f2) <= 16:
                    fin = f2
                else:
                    return r['end'], 'MPF_ROTO'
    except Exception:
        return r['end'], 'MPF_ILEGIBLE'
    return fin, ('MPF' if fin > r['end'] else '')


class Tif:
    def __init__(self, d, base):
        self.d, self.base, self.ok = d, base, False
        t = d[base: base + 2]
        if base + 8 <= len(d) and t in (b'II', b'MM'):
            self.e = '<' if t == b'II' else '>'
            self.ok = struct.unpack_from(self.e + 'H', d, base + 2)[0] == 42

    def u16(self, o):
        return struct.unpack_from(self.e + 'H', self.d, o)[0]

    def u32(self, o):
        return struct.unpack_from(self.e + 'I', self.d, o)[0]

    def ifd(self, rel):
        out, o = {}, self.base + rel
        if o + 2 > len(self.d):
            return out
        n = self.u16(o)
        if n > 600:
            return out
        for k in range(n):
            p = o + 2 + 12 * k
            if p + 12 > len(self.d):
                break
            tag, typ, cnt = self.u16(p), self.u16(p + 2), self.u32(p + 4)
            vpos = p + 8 if TSIZE.get(typ, 1) * cnt <= 4 else self.base + self.u32(p + 8)
            out[tag] = (typ, cnt, vpos)
        return out

    def val(self, ent):
        typ, cnt, pos = ent
        if typ == 2:
            return bytes(self.d[pos: pos + cnt]).split(b'\0')[0].decode('latin-1').strip()
        if typ == 3 and cnt >= 1:
            return self.u16(pos)
        if typ == 4 and cnt >= 1:
            return self.u32(pos)
        return None


def exif_info(d, r):
    """Marca, modelo, fecha de disparo y, en Canon, el numero de archivo (MakerNote 0x0008: carpeta*10000 + numero)."""
    out = {}
    try:
        if not r['exif']:
            return out
        off = r['exif'][0]
        t = Tif(d, off)
        if not t.ok:
            return out
        i0 = t.ifd(t.u32(off + 4))
        out['marca'] = t.val(i0[0x010F]) if 0x010F in i0 else None
        out['modelo'] = t.val(i0[0x0110]) if 0x0110 in i0 else None
        ex = t.ifd(t.val(i0[0x8769])) if 0x8769 in i0 else {}
        for tag in (0x9003, 0x9004):
            if tag in ex:
                out['fecha_exif'] = t.val(ex[tag])
                break
        if 0x927C in ex and (out.get('marca') or '').upper().startswith('CANON'):
            mn = t.ifd(ex[0x927C][2] - off)
            if 0x0008 in mn:
                out['canon_numero'] = t.val(mn[0x0008])
    except Exception:
        pass
    return out


def nombre_canon(num):
    """100CANON/IMG_0001.JPG <- numero de archivo 1000001 (carpeta 100, foto 1)."""
    if not num:
        return None
    return 'IMG_%04d.JPG' % (num % 10000), num // 10000


def decodifica(data):
    """Decodifica a 1/8 de tamano (rapido, pero recorre toda la entropia: un JPG cortado da error). -> (ok, detalle)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        im.draft('RGB', (max(1, w // 8), max(1, h // 8)))
        im.load()
        return True, ''
    except Exception as ex:
        return False, str(ex)[:80]


def analizar(data, deco=True):
    r = jpeg_walk(data, 0)
    res = dict(ok=r['ok'], motivo=r['motivo'], w=r['w'], h=r['h'], fin=None, nota='')
    if r['ok']:
        res['fin'], res['nota'] = mpf_fin(data, r)
    res.update(exif_info(data, r))
    if deco and r['ok']:
        res['deco'], res['deco_msg'] = decodifica(data[:res['fin']])
    return res


def fecha_a_epoch(txt):
    m = re.match(r'(\d{4})[:-](\d\d)[:-](\d\d)[ T](\d\d):(\d\d):(\d\d)', txt or '')
    if not m:
        return None
    try:
        return time.mktime(tuple(int(x) for x in m.groups()) + (0, 0, -1))
    except Exception:
        return None


def dif_fechas(a, b):
    ea, eb = fecha_a_epoch(a), fecha_a_epoch(b)
    return abs(ea - eb) if ea is not None and eb is not None else None


def limpio(n):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', n).strip(' .') or 'sin_nombre'


# ---------------------------------------------------------------- comandos

def cargar_trabajo(trab):
    p = os.path.join(trab, 'escaneo.json')
    if not os.path.exists(p):
        raise SystemExit('Falta %s: correr antes "escanear".' % p)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def cmd_info(a):
    vol = Vol(a.imagen)
    say('== INFO == %s' % a.imagen)
    say('  volumen exFAT: %d clusters de %d KB, datos desde el byte %d, FAT en %d, raiz en cluster %d, serie %08X' % (
        vol.ncl, vol.csize // 1024, vol.base + vol.heap_off, vol.base + vol.fat_off, vol.root, vol.serial))
    say('  tamano del volumen %s | uso segun boot sector: %d %%' % (human(vol.vol_len), vol.pct))
    info, files = arbol(vol)
    say('  etiqueta: %s | directorios recorridos: %d | entradas vivas: %d' % (info['label'], info['dirs'], len(files)))
    bm = leer_bitmap(vol, info)
    occ = mapa_ocupado(vol, info, files)
    if bm is not None:
        sin_dueno = int((bm & ~occ).sum())
        say('  bitmap: %d clusters ocupados (%s) | reclamados por archivos vivos y sistema: %d | OCUPADOS SIN DUENO: %d (%s)' % (
            int(bm.sum()), human(int(bm.sum()) * vol.csize), int(occ.sum()), sin_dueno, human(sin_dueno * vol.csize)))
        libres_con_dueno = int((~bm & occ).sum())
        if libres_con_dueno:
            say('  OJO: %d clusters que archivos vivos reclaman figuran como libres en el bitmap' % libres_con_dueno)
    say('  --- entradas vivas ---')
    for s in sorted(files, key=lambda x: x['ruta'])[:a.max]:
        say('  %-44s %12d B  cluster %-8s %s  %s' % (
            s['ruta'][:44], s['dlen'], s['first'], s['modif'] or '-', 'DIR' if s['attr'] & 0x10 else ''))
    if len(files) > a.max:
        say('  ... y %d mas' % (len(files) - a.max))


def cmd_escanear(a):
    vol = Vol(a.imagen)
    os.makedirs(a.trabajo, exist_ok=True)
    say('== ESCANEAR == %s (%d clusters de %d KB, %s)' % (a.imagen, vol.ncl, vol.csize // 1024, human(vol.ncl * vol.csize)))
    info, tree = arbol(vol)
    live = {s['off'] for s in tree}
    bm = leer_bitmap(vol, info)
    occ = mapa_ocupado(vol, info, tree)
    say('  etiqueta %s | %d entradas vivas en el arbol' % (info['label'], len(tree)))
    ch = max(1, (64 << 20) // vol.csize)
    over = 32 * 20
    c_ini = max(2, a.c0)
    c_fin = vol.ncl + 2 if not a.n else min(vol.ncl + 2, c_ini + a.n)
    zero = np.zeros(vol.ncl, dtype=bool)
    sets, heads, sig = {}, [], Counter()
    t0 = time.time()
    nextp = 0
    for c0 in range(c_ini, c_fin, ch):
        n = min(ch, c_fin - c0)
        L = n * vol.csize
        buf = vol.rd(vol.coff(c0), L + over)
        if len(buf) < L + over:
            buf = buf.ljust(L + over, b'\0')
        a8 = np.frombuffer(buf, dtype=np.uint8)
        a2 = a8[:L].reshape(n, vol.csize)
        zero[c0 - 2: c0 - 2 + n] = ~a8[:L].view(np.uint64).reshape(n, vol.csize // 8).any(axis=1)
        jh = (a2[:, 0] == 0xFF) & (a2[:, 1] == 0xD8) & (a2[:, 2] == 0xFF)
        heads.extend((np.nonzero(jh)[0] + c0).tolist())
        ftyp = (a2[:, 4] == 0x66) & (a2[:, 5] == 0x74) & (a2[:, 6] == 0x79) & (a2[:, 7] == 0x70)
        tif = (a2[:, 0] == 0x49) & (a2[:, 1] == 0x49) & (a2[:, 2] == 0x2A) & (a2[:, 3] == 0)
        sig['JPEG'] += int(jh.sum())
        sig['ftyp (CR3/MP4/MOV/HEIC)'] += int(ftyp.sum())
        sig['TIFF (ARW/NEF/CR2/DNG)'] += int(tif.sum())
        t = a8[:L + 32:32]
        m = ((t[:-1] == 0x85) | (t[:-1] == 0x05)) & ((t[1:] == 0xC0) | (t[1:] == 0x40))
        for i in np.nonzero(m)[0].tolist():
            s = parse_set(buf, 32 * i, vol.ncl)
            if s:
                s['off'] = vol.coff(c0) + 32 * i
                s['cl'] = c0 + (32 * i) // vol.csize
                s['estado'] = 'borrado' if s['deleted'] else ('vivo' if s['off'] in live else 'huerfano')
                sets[s['off']] = s
        done = (c0 + n - c_ini) * vol.csize
        if done >= nextp:
            el = max(time.time() - t0, 1e-6)
            say('  %6.0f MB (%.0f MB/s)  entradas %d  cabezas JPEG %d' % (done / 1e6, done / 1e6 / el, len(sets), len(heads)))
            nextp += 4 << 30
    sl = sorted(sets.values(), key=lambda s: s['off'])
    np.save(os.path.join(a.trabajo, 'ceros.npy'), zero)
    jp = [s for s in sl if s['name'].lower().endswith(('.jpg', '.jpeg', '.jpe'))]
    res = dict(imagen=os.path.abspath(a.imagen), ncl=vol.ncl, csize=vol.csize, etiqueta=info['label'], rango=[c_ini, c_fin],
               ceros=int(zero[c_ini - 2: c_fin - 2].sum()), ocupados_bitmap=int(bm.sum()) if bm is not None else None,
               ocupados_con_dueno=int(occ.sum()), firmas=dict(sig), cabezas_jpeg=heads, entradas=sl,
               vivas=[s for s in tree], segundos=round(time.time() - t0))
    with open(os.path.join(a.trabajo, 'escaneo.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f)
    with open(os.path.join(a.trabajo, 'entradas.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['estado', 'nombre', 'bytes', 'cluster_inicial', 'sin_cadena_fat', 'modificado', 'creado', 'cluster_donde_estaba'])
        for s in sl:
            w.writerow([s['estado'], s['name'], s['dlen'], s['first'], int(s['nofat']), s['modif'], s['creado'], s['cl']])
    nz = (c_fin - c_ini) - res['ceros']
    say('== RESULTADO ==  %.0f s' % (time.time() - t0))
    say('  clusters leidos %d | en cero %d (%.1f %%) | con datos %d (%s)' % (
        c_fin - c_ini, res['ceros'], 100.0 * res['ceros'] / max(1, c_fin - c_ini), nz, human(nz * vol.csize)))
    say('  firmas al comienzo de un cluster: %s' % dict(sig))
    est = Counter(s['estado'] for s in sl)
    estj = Counter(s['estado'] for s in jp)
    say('  conjuntos de entradas de directorio validos: %d (%s) | de .JPG: %d (%s)' % (len(sl), dict(est), len(jp), dict(estj)))
    por_cl = Counter(s['cl'] for s in sl)
    for c, k in por_cl.most_common(8):
        grp = [s for s in sl if s['cl'] == c]
        fechas = sorted(s['modif'] for s in grp if s['modif'])
        say('    cluster %-8d %5d entradas  (%s)  %s .. %s' % (
            c, k, dict(Counter(s['estado'] for s in grp)), fechas[0] if fechas else '-', fechas[-1] if fechas else '-'))


def tomar_candidatos(res):
    """Entradas .JPG sin duplicados (mismo nombre, cluster y tamano), preferiendo la viva."""
    vistos = {}
    for s in res['entradas']:
        if not s['name'].lower().endswith(('.jpg', '.jpeg', '.jpe')) or not s['first'] or not s['dlen']:
            continue
        k = (s['name'].lower(), s['first'], s['dlen'])
        if k in vistos:
            vistos[k]['copias'] += 1
            if s['estado'] == 'vivo':
                vistos[k].update(estado='vivo')
        else:
            s = dict(s)
            s['copias'] = 1
            vistos[k] = s
    return sorted(vistos.values(), key=lambda s: (s['modif'] or '', s['first']))


def calcular_pisadas(vol, res, cands):
    """Si dos entradas reclaman los mismos clusters, la mas vieja (por fecha) quedo pisada por la mas nueva en esa parte:
    la camara reutiliza el espacio de las fotos borradas. Anota en cada candidata sus clusters reales ('_clusters', con la
    cadena FAT si esta fragmentada) y 'pisada_por' (nombres de las mas nuevas que comparten alguno).
    Se consideran TODAS las entradas de archivo (JPG, CR2, lo que sea), no solo las .JPG."""
    dueno, vistos = {}, set()
    for s in res['entradas']:
        if s['attr'] & 0x10 or not s['first'] or not s['dlen']:
            continue
        k = (s['name'].lower(), s['first'], s['dlen'])
        if k in vistos:
            continue
        vistos.add(k)
        for c in vol.entry_clusters(s)[0]:
            dueno.setdefault(c, []).append((s['modif'] or '', s['name'], s['first']))
    for s in cands:
        mine = (s['modif'] or '', s['name'])
        s['_clusters'], s['_nota_cadena'] = vol.entry_clusters(s)
        por = []
        for c in s['_clusters']:
            for t in dueno.get(c, ()):
                if (t[1].lower(), t[2]) != (s['name'].lower(), s['first']) and (t[0], t[1]) > mine and t[1] not in por:
                    por.append(t[1])
        s['pisada_por'] = por


def cmd_extraer(a):
    vol = Vol(a.imagen)
    res = cargar_trabajo(a.trabajo)
    info, tree = arbol(vol)
    occ = mapa_ocupado(vol, info, tree)
    cands = tomar_candidatos(res)
    calcular_pisadas(vol, res, cands)
    if a.limite:
        cands = cands[:a.limite]
    say('== EXTRAER POR ENTRADA == %d candidatas .JPG (%d con cadena FAT)' % (len(cands), sum(1 for s in cands if not s['nofat'])))
    os.makedirs(a.out, exist_ok=True)
    cubiertos = np.zeros(vol.ncl, dtype=bool)
    filas, usados = [], Counter()
    for s in cands:
        cls = s['_clusters']
        data = vol.read_list(cls, s['dlen'])
        pisado = 0 if s['estado'] == 'vivo' else int(sum(1 for c in cls if occ[c - 2]))
        an = analizar(data) if len(data) >= 4 else dict(ok=False, motivo='vacio', deco=False)
        dd = dif_fechas(an.get('fecha_exif'), s['modif'])
        detalle = an.get('motivo') or an.get('deco_msg') or ''
        notas = '; '.join(x for x in (an.get('nota'), s['_nota_cadena']) if x)
        if not an['ok']:
            estado = 'ROTA'
        elif not an.get('deco'):
            estado = 'DANADA'
        elif pisado or s['pisada_por']:
            estado = 'DUDOSA_PISADA'
            detalle = 'comparte clusters con %s' % ('archivos vivos' if pisado else 'entradas mas nuevas')
        elif dd is not None and dd > 600:
            estado = 'DUDOSA_FECHA'
            detalle = 'fecha EXIF y de la entrada difieren %d s' % dd
        elif an['fin'] != s['dlen'] and not an.get('nota', '').startswith('MPF'):
            estado = 'COMPLETA_CON_COLA'
            detalle = 'la imagen termina a los %d bytes y la entrada dice %d' % (an['fin'], s['dlen'])
        else:
            estado = 'COMPLETA'
            detalle = notas
        base = limpio(s['name'])
        usados[base.lower()] += 1
        sub = {'COMPLETA': '', 'COMPLETA_CON_COLA': ''}.get(estado, 'REVISAR')
        carp = os.path.join(a.out, sub) if sub else a.out
        os.makedirs(carp, exist_ok=True)
        nom = base if usados[base.lower()] == 1 else '%s__c%d%s' % (os.path.splitext(base)[0], s['first'], os.path.splitext(base)[1])
        ruta = os.path.join(carp, nom)
        if len(data) >= 4 and data[:2] == b'\xff\xd8':
            with open(ruta, 'wb') as f:
                f.write(data)
            ep = fecha_a_epoch(an.get('fecha_exif') or s['modif'])
            if ep:
                os.utime(ruta, (ep, ep))
        else:
            ruta = ''
        s['_estado'], s['_ruta'] = estado, ruta
        if estado.startswith('COMPLETA'):
            for c in cls:
                cubiertos[c - 2] = True
        filas.append([estado, s['estado'], s['name'], s['first'], s['dlen'], s['modif'], an.get('fecha_exif'),
                      '' if dd is None else int(dd), an.get('marca'), an.get('modelo'), an.get('w'), an.get('h'),
                      pisado, ';'.join(s['pisada_por'][:4]), s['copias'], detalle, ruta])
    with open(os.path.join(a.trabajo, 'extraidas.json'), 'w', encoding='utf-8') as f:
        json.dump([dict(first=s['first'], dlen=s['dlen'], estado=s['_estado'], ruta=s['_ruta'], name=s['name']) for s in cands], f)
    np.save(os.path.join(a.trabajo, 'cubiertos_entradas.npy'), cubiertos)
    with open(os.path.join(a.trabajo, 'informe_entradas.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['estado', 'origen_entrada', 'nombre', 'cluster', 'bytes', 'fecha_entrada', 'fecha_exif', 'dif_seg', 'marca',
                    'modelo', 'ancho', 'alto', 'clusters_pisados_por_vivos', 'compartida_con_mas_nuevas', 'copias', 'detalle', 'archivo'])
        w.writerows(filas)
    say('  resultado por estado: %s' % dict(Counter(r[0] for r in filas)))
    say('  por origen de la entrada: %s' % dict(Counter(r[1] for r in filas)))
    say('  salida: %s | informe: %s' % (a.out, os.path.join(a.trabajo, 'informe_entradas.csv')))


def leer_ventana(vol, c, nb):
    return vol.rd(vol.coff(c), nb)


def cmd_carvar(a):
    """Un JPG por cada cabeza alineada a cluster que las entradas no cubrieron. Cada archivo se acota a la proxima cabeza:
    un JPG real no contiene otra cabeza al comienzo de un cluster, asi que si la hay, el archivo que venia terminaba ahi
    (lo pisaron) y lo que queda es PARCIAL."""
    vol = Vol(a.imagen)
    res = cargar_trabajo(a.trabajo)
    heads = sorted(res['cabezas_jpeg'])
    cub = np.zeros(vol.ncl, dtype=bool)
    p = os.path.join(a.trabajo, 'cubiertos_entradas.npy')
    if os.path.exists(p):
        cub |= np.load(p)
    por_cl = {}
    for s in res['entradas']:
        if s['name'].lower().endswith(('.jpg', '.jpeg', '.jpe')) and s['first']:
            por_cl.setdefault(s['first'], []).append(s)
    pend = [k for k, c in enumerate(heads) if not cub[c - 2]]
    if a.limite:
        pend = pend[:a.limite]
    say('== CARVAR POR CABECERA == %d cabezas JPEG alineadas a cluster, %d ya cubiertas por entradas, %d a carvar' % (
        len(heads), len(heads) - sum(1 for c in heads if not cub[c - 2]), len(pend)))
    os.makedirs(a.out, exist_ok=True)
    filas, nombres = [], Counter()
    coinciden = [0, 0]
    t0 = time.time()
    for n_, k in enumerate(pend):
        c = heads[k]
        if cub[c - 2]:
            continue
        limite = ((heads[k + 1] if k + 1 < len(heads) else vol.ncl + 2) - c) * vol.csize
        tope = min(192 << 20, limite)
        ventana = min(6 << 20, limite)
        while True:
            data = leer_ventana(vol, c, ventana)
            r = jpeg_walk(data, 0)
            if r['ok']:
                fin, nota = mpf_fin(data, r)
                if nota.startswith('MPF_FUERA') and ventana < tope:
                    ventana = min(ventana * 2, tope)
                    continue
                break
            if r['motivo'].startswith('cortado') and ventana < tope:
                ventana = min(ventana * 2, tope)
                continue
            break
        ex = exif_info(data, r)
        ents = por_cl.get(c, [])
        ent = None
        if ents:
            ent = min(ents, key=lambda s: (dif_fechas(ex.get('fecha_exif'), s['modif']) if dif_fechas(ex.get('fecha_exif'), s['modif']) is not None else 1e12))
            dd = dif_fechas(ex.get('fecha_exif'), ent['modif'])
            if dd is not None and dd > 600:
                ent = None                      # la entrada es de otra foto que estuvo ahi antes: el nombre no sirve
        estado, nota = None, ''
        if r['ok']:
            fin, nota = mpf_fin(data, r)
            data = data[:fin]
            if len(data) < a.min_kb * 1024:
                filas.append(['CHICA', c, len(data), 'menor que --min-kb', '', '', '', '', '', '', ''])
                continue
            an = analizar(data)
            estado = 'COMPLETA' if an.get('deco') else 'DANADA'
            nota = nota or an.get('deco_msg') or ''
        elif r['motivo'].startswith('cortado') and len(data) >= limite and r['nscan'] >= 1 and r['w'] and len(data) >= a.min_kb * 1024:
            estado = 'PARCIAL'
            nota = 'llega la proxima cabeza a los %d KB: lo que sigue es de otra foto' % (len(data) // 1024)
            data = data + b'\xff\xd9'
        else:
            filas.append(['SIN_FIN', c, '', r['motivo'], ex.get('fecha_exif'), ex.get('marca'), ex.get('modelo'), '', '', '', ''])
            continue
        num = ex.get('canon_numero')
        nc = nombre_canon(num) if num else None
        if ent:
            base, deducido = limpio(ent['name']), 'entrada de directorio'
            if nc:
                coinciden[1] += 1
                coinciden[0] += int(nc[0].lower() == base.lower())
        elif nc:
            base, deducido = nc[0], 'EXIF Canon (carpeta %dCANON)' % nc[1]
        else:
            f = (ex.get('fecha_exif') or 'SINFECHA').replace(':', '').replace(' ', '_')
            base, deducido = 'RECUP_%s_c%d.JPG' % (f, c), 'sin numero de archivo en EXIF'
        nombres[base.lower()] += 1
        nom = base if nombres[base.lower()] == 1 else '%s__c%d%s' % (os.path.splitext(base)[0], c, os.path.splitext(base)[1])
        carp = a.out if estado == 'COMPLETA' else os.path.join(a.out, 'PARCIALES' if estado == 'PARCIAL' else 'REVISAR')
        os.makedirs(carp, exist_ok=True)
        ruta = os.path.join(carp, nom)
        with open(ruta, 'wb') as fo:
            fo.write(data)
        ep = fecha_a_epoch(ex.get('fecha_exif'))
        if ep:
            os.utime(ruta, (ep, ep))
        if estado == 'COMPLETA':
            cub[c - 2: c - 2 + (len(data) + vol.csize - 1) // vol.csize] = True
        filas.append([estado, c, len(data), nota, ex.get('fecha_exif'), ex.get('marca'), ex.get('modelo'),
                      '%sx%s' % (r['w'], r['h']), deducido, ent['name'] if ent else '', ruta])
        if (n_ + 1) % 250 == 0:
            say('  %d/%d cabezas, %d archivos, %.0f s' % (n_ + 1, len(pend), sum(1 for x in filas if x[0] in ('COMPLETA', 'PARCIAL', 'DANADA')), time.time() - t0))
    with open(os.path.join(a.trabajo, 'informe_carving.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['estado', 'cluster', 'bytes', 'detalle', 'fecha_exif', 'marca', 'modelo', 'dimensiones', 'nombre_deducido_de',
                    'nombre_entrada', 'archivo'])
        w.writerows(filas)
    say('  resultado: %s | %.0f s' % (dict(Counter(r[0] for r in filas)), time.time() - t0))
    if coinciden[1]:
        say('  nombre sacado del EXIF Canon vs nombre de la entrada de directorio: coinciden %d de %d' % tuple(coinciden))
    say('  salida: %s | informe: %s' % (a.out, os.path.join(a.trabajo, 'informe_carving.csv')))


def cmd_hojas(a):
    from PIL import Image, ImageDraw, ImageOps
    files = []
    for root, _, fns in os.walk(a.carpeta):
        for fn in fns:
            if fn.lower().endswith(('.jpg', '.jpeg')):
                files.append(os.path.join(root, fn))
    files.sort()
    os.makedirs(a.out, exist_ok=True)
    tw, th, cols, rows = a.ancho, int(a.ancho * 0.75), a.cols, a.filas
    per = cols * rows
    say('== HOJAS == %d fotos -> %d hojas' % (len(files), (len(files) + per - 1) // per))
    for k in range(0, len(files), per):
        sheet = Image.new('RGB', (cols * tw, rows * (th + 16)), (24, 24, 24))
        dr = ImageDraw.Draw(sheet)
        for j, p in enumerate(files[k:k + per]):
            x, y = (j % cols) * tw, (j // cols) * (th + 16)
            try:
                im = Image.open(p)
                im.draft('RGB', (tw * 2, th * 2))
                im = ImageOps.exif_transpose(im.convert('RGB'))
                im.thumbnail((tw - 4, th - 4))
                sheet.paste(im, (x + 2 + (tw - 4 - im.width) // 2, y + 2 + (th - 4 - im.height) // 2))
            except Exception as ex:
                dr.text((x + 6, y + 6), 'ERROR %s' % str(ex)[:30], fill=(255, 80, 80))
            dr.text((x + 4, y + th), os.path.basename(p)[:28], fill=(220, 220, 220))
        out = os.path.join(a.out, 'hoja_%03d.jpg' % (k // per + 1))
        sheet.save(out, quality=82)
        say('  %s  (%d..%d)' % (out, k + 1, min(k + per, len(files))))


def main():
    global LOG
    ap = argparse.ArgumentParser(description='Rescate de JPG de una tarjeta exFAT, sobre su imagen (solo lectura).')
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('info')
    s.add_argument('imagen')
    s.add_argument('--max', type=int, default=60)
    s = sp.add_parser('escanear')
    s.add_argument('imagen')
    s.add_argument('--trabajo', required=True)
    s.add_argument('--c0', type=int, default=2)
    s.add_argument('--n', type=int, default=0, help='cuantos clusters escanear (0 = todos)')
    for name in ('extraer', 'carvar'):
        s = sp.add_parser(name)
        s.add_argument('imagen')
        s.add_argument('--trabajo', required=True)
        s.add_argument('--out', required=True)
        if name == 'carvar':
            s.add_argument('--min-kb', type=int, default=60, help='descartar JPG menores a esto (miniaturas)')
        s.add_argument('--limite', type=int, default=0, help='solo las primeras N (para probar)')
    s = sp.add_parser('hojas')
    s.add_argument('carpeta')
    s.add_argument('--out', required=True)
    s.add_argument('--ancho', type=int, default=240)
    s.add_argument('--cols', type=int, default=8)
    s.add_argument('--filas', type=int, default=5)
    for s in sp.choices.values():
        s.add_argument('--log')
    a = ap.parse_args()
    if a.log:
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
        LOG = open(a.log, 'w', encoding='utf-8', errors='replace')
    code = 0
    try:
        {'info': cmd_info, 'escanear': cmd_escanear, 'extraer': cmd_extraer, 'carvar': cmd_carvar, 'hojas': cmd_hojas}[a.cmd](a)
    except SystemExit as ex:
        say('ERROR: %s' % ex)
        code = 1
    except Exception:
        say(traceback.format_exc())
        code = 2
    say('== FIN (codigo %d) ==' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
