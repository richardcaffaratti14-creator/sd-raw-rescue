#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rescatar_sd.py - rescate de fotos RAW (Sony ARW/SR2, Nikon NEF...) de una SD FAT32 con directorios rotos.

La tarjeta SOLO SE LEE. Todo lo que se escribe va a --out y --log.
Leer la tarjeta cruda en Windows exige una consola como Administrador.

  python rescatar_sd.py image   E: [--out tarjeta.img]          copia 1:1 de la tarjeta a un archivo (hacer esto primero)
  python rescatar_sd.py scan    E: [--ref CARPETA_PHOTOREC]     diagnostico de la FAT (no escribe fotos)
  python rescatar_sd.py extract E: [--out DIR] [--ref CARPETA]  saca TODAS las fotos siguiendo la FAT
  python rescatar_sd.py thumbs  ARCHIVO_O_CARPETA [--out DIR]   saca los JPEG embebidos de ARW/SR2 (sin tarjeta)

Idea: las fotos de una camara quedan fragmentadas (trozos de otras fotos en el medio). PhotoRec carva
sectores contiguos y por eso las rompe. La FAT guarda el orden real de los clusters de cada archivo, aunque
los nombres del directorio esten rotos: seguir esa cadena rearma el archivo original.
"""
import argparse
import csv
import hashlib
import os
import re
import struct
import sys
import time
import traceback

SEC = 512
EOC = 0x0FFFFFF8
BADC = 0x0FFFFFF7
MASK = 0x0FFFFFFF
PHOTO = ('ARW', 'NEF', 'CR2', 'TIFF', 'JPG')
LOG = None


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


# ---------------------------------------------------------------- lectura cruda

class Source:
    """Volumen (\\\\.\\E:), disco o archivo imagen. Lecturas alineadas a 512 bytes."""

    def __init__(self, spec):
        s = spec.strip().strip('"')
        if re.fullmatch(r'[A-Za-z]:?\\?', s):
            s = '\\\\.\\%s:' % s[0].upper()
        self.path = s
        try:
            self.f = open(s, 'rb', buffering=0)
        except PermissionError:
            raise SystemExit('SIN PERMISO para leer %s. Abri la consola como Administrador.' % s)
        except OSError as ex:
            raise SystemExit('No pude abrir %s: %s' % (s, ex))
        self.bad_sectors = 0

    def _slow(self, off, n):
        out = bytearray()
        for p in range(off, off + n, SEC):
            try:
                self.f.seek(p)
                b = self.f.read(SEC)
            except OSError:
                b = b''
                self.bad_sectors += 1
            out += b.ljust(SEC, b'\0')
        return bytes(out)

    def read(self, off, n):
        if n <= 0:
            return b''
        a = off & ~(SEC - 1)
        e = (off + n + SEC - 1) & ~(SEC - 1)
        try:
            self.f.seek(a)
            buf = bytearray()
            while len(buf) < e - a:
                chunk = self.f.read(e - a - len(buf))
                if not chunk:
                    break
                buf += chunk
            buf = bytes(buf).ljust(e - a, b'\0')
        except OSError:
            buf = self._slow(a, e - a)
        return buf[off - a: off - a + n]


# ---------------------------------------------------------------- FAT32

class Fat32:
    def __init__(self, src, which=1):
        self.src = src
        self.base = 0
        b = src.read(0, SEC)
        if b[510:512] != b'\x55\xaa':
            raise SystemExit('Sector 0 sin firma 55AA: no es FAT32 ni un disco con MBR.')
        if b[82:90] != b'FAT32   ':
            for i in range(4):
                ent = b[446 + 16 * i: 462 + 16 * i]
                if ent[4] in (0x0B, 0x0C):
                    self.base = struct.unpack_from('<I', ent, 8)[0] * SEC
                    break
            b = src.read(self.base, SEC)
            if self.base == 0 or b[82:90] != b'FAT32   ':
                raise SystemExit('No encontre un volumen FAT32' + (
                    ' (parece exFAT: tarjeta de 64 GB o mas). Con exFAT solo funcionan carve_cabeceras.py y mapa_clusters.py, '
                    'sin FAT ni directorio.' if exfat_geometry(src) else '.'))
        self.bps, self.spc, self.rsv, self.nf = struct.unpack_from('<HBHB', b, 11)
        self.tot = struct.unpack_from('<I', b, 32)[0]
        self.fsz = struct.unpack_from('<I', b, 36)[0]
        self.root = struct.unpack_from('<I', b, 44)[0]
        self.fsinfo = struct.unpack_from('<H', b, 48)[0]
        if self.bps != SEC or self.spc == 0 or self.nf == 0:
            raise SystemExit('Boot sector con valores raros (bps=%d spc=%d fats=%d).' % (self.bps, self.spc, self.nf))
        self.csize = self.bps * self.spc
        self.fat_off = self.rsv * self.bps
        self.data_off = (self.rsv + self.nf * self.fsz) * self.bps
        self.ncl = (self.tot - self.rsv - self.nf * self.fsz) // self.spc
        nent = min(self.fsz * self.bps // 4, self.ncl + 2)
        self.lim = nent
        self.fats = []
        for k in range(self.nf):
            raw = self.rd(self.fat_off + k * self.fsz * self.bps, nent * 4)
            self.fats.append(memoryview(raw).cast('I'))
        self.fat = self.fats[min(which, self.nf) - 1]

    def rd(self, off, n):
        return self.src.read(self.base + off, n)

    def coff(self, c):
        return self.data_off + (c - 2) * self.csize

    def fsinfo_info(self):
        if not self.fsinfo:
            return None
        b = self.rd(self.fsinfo * self.bps, SEC)
        if b[0:4] != b'RRaA' or b[484:488] != b'rrAa':
            return None
        return dict(free=struct.unpack_from('<I', b, 488)[0], nxt=struct.unpack_from('<I', b, 492)[0])

    def chain(self, start):
        out = []
        c = start
        while 2 <= c < self.lim:
            out.append(c)
            if len(out) > self.ncl:
                return out, 'LOOP'
            v = self.fat[c] & MASK
            if v >= EOC:
                return out, 'EOC'
            if v == 0:
                return out, 'FREE'
            if v == BADC:
                return out, 'BAD'
            c = v
        return out, 'BADPTR'

    def stats(self, start):
        fat, lim = self.fat, self.lim
        n, frags, c = 0, 1, start
        while True:
            if not (2 <= c < lim):
                return n, frags, 'BADPTR'
            n += 1
            if n > self.ncl:
                return n, frags, 'LOOP'
            v = fat[c] & MASK
            if v >= EOC:
                return n, frags, 'EOC'
            if v == 0:
                return n, frags, 'FREE'
            if v == BADC:
                return n, frags, 'BAD'
            if v != c + 1:
                frags += 1
            c = v

    def read_clusters(self, cl):
        parts = []
        i, n = 0, len(cl)
        maxrun = max(1, (8 << 20) // self.csize)
        while i < n:
            j = i + 1
            while j < n and cl[j] == cl[j - 1] + 1 and j - i < maxrun:
                j += 1
            parts.append(self.rd(self.coff(cl[i]), (j - i) * self.csize))
            i = j
        return b''.join(parts)

    def find_heads(self):
        fat, lim = self.fat, self.lim
        pred = bytearray(lim)
        st = dict(free=0, alloc=0, bad=0, inval=0)
        for c in range(2, lim):
            v = fat[c] & MASK
            if v == 0:
                st['free'] += 1
                continue
            st['alloc'] += 1
            if v == BADC:
                st['bad'] += 1
            elif v >= EOC:
                pass
            elif 2 <= v < lim:
                pred[v] = 1
            else:
                st['inval'] += 1
        heads = [c for c in range(2, lim) if (fat[c] & MASK) not in (0, BADC) and not pred[c]]
        return heads, st

    def dir_entries(self, start, maxclusters=64):
        cl, _ = self.chain(start)
        data = self.read_clusters(cl[:maxclusters])
        out = []
        for i in range(0, len(data), 32):
            e = data[i:i + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5 or e[11] == 0x0F:
                continue
            name = e[0:8].decode('cp437', 'replace').rstrip()
            ext = e[8:11].decode('cp437', 'replace').rstrip()
            hi = struct.unpack_from('<H', e, 20)[0]
            lo = struct.unpack_from('<H', e, 26)[0]
            out.append(dict(name=name + ('.' + ext if ext else ''), attr=e[11], c=(hi << 16) | lo,
                            size=struct.unpack_from('<I', e, 28)[0]))
        return out


def exfat_geometry(src):
    """Geometria de un volumen exFAT (tarjetas de 64 GB o mas) o None: lo justo para recorrer clusters."""
    b = src.read(0, SEC)
    base = 0
    if b[3:11] != b'EXFAT   ':
        if b[510:512] != b'\x55\xaa':
            return None
        for i in range(4):
            ent = b[446 + 16 * i: 462 + 16 * i]
            if ent[4] == 0x07:
                cand = struct.unpack_from('<I', ent, 8)[0] * SEC
                bb = src.read(cand, SEC)
                if bb[3:11] == b'EXFAT   ':
                    base, b = cand, bb
                    break
        else:
            return None
    heap_off, ncl = struct.unpack_from('<II', b, 88)           # ClusterHeapOffset (sectores), ClusterCount
    bps_shift, spc_shift = b[108], b[109]
    if not (9 <= bps_shift <= 12) or spc_shift > 25 or not ncl:
        return None
    bps = 1 << bps_shift
    return dict(base=base, data_off=heap_off * bps, csize=bps << spc_shift, ncl=ncl)


class Plano(Fat32):
    """Volumen sin FAT32 legible (exFAT): solo geometria de clusters, sin FAT ni directorio. Sirve para mapear y carvar."""

    def __init__(self, src, base, data_off, csize, ncl):
        self.src, self.base, self.data_off, self.csize, self.ncl = src, base, data_off, csize, ncl
        self.bps, self.spc = SEC, csize // SEC
        self.lim = ncl + 2
        self.tot = (data_off + ncl * csize) // SEC
        self.fat = None


def abrir_volumen(src, which=1):
    """Fat32 si se puede; si es exFAT, un Plano (sin FAT). Para herramientas que solo necesitan la geometria."""
    try:
        return Fat32(src, which)
    except SystemExit:
        g = exfat_geometry(src)
        if g:
            return Plano(src, **g)
        raise


def classify(b):
    if len(b) < 16:
        return 'ZERO' if not any(b) else 'OTRO'
    if b[:4] in (b'II*\x00', b'MM\x00*'):
        h = b[:8192]
        if b'SONY' in h:
            return 'ARW'
        if b'NIKON' in h:
            return 'NEF'
        if b'Canon' in h:
            return 'CR2'
        return 'TIFF'
    if b[:3] == b'\xff\xd8\xff':
        return 'JPG'
    if len(b) > 200 and b[4] == 0x47 and b[196] == 0x47:
        return 'M2TS'
    if b[:11] == b'.          ' and b[32:43] == b'..         ':
        return 'DIR'
    if not any(b[:512]):
        return 'ZERO'
    return 'OTRO'


# ---------------------------------------------------------------- TIFF (estructura de ARW/NEF)

class Tiff:
    SZ = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8}

    def __init__(self, d):
        self.d = d
        self.ok = False
        if len(d) < 16 or d[:2] not in (b'II', b'MM'):
            return
        self.e = '<' if d[:2] == b'II' else '>'
        if struct.unpack_from(self.e + 'H', d, 2)[0] != 42:
            return
        self.ifd0 = struct.unpack_from(self.e + 'I', d, 4)[0]
        self.ok = True

    def ifd(self, off):
        d, e = self.d, self.e
        out = {}
        if off < 8 or off + 2 > len(d):
            return out
        n = struct.unpack_from(e + 'H', d, off)[0]
        if n > 1000:
            return out
        for i in range(n):
            p = off + 2 + 12 * i
            if p + 12 > len(d):
                break
            tag, typ, cnt = struct.unpack_from(e + 'HHI', d, p)
            size = self.SZ.get(typ, 1) * cnt
            vo = p + 8 if size <= 4 else struct.unpack_from(e + 'I', d, p + 8)[0]
            out[tag] = (typ, cnt, vo)
        return out

    def ints(self, ent, cap=200000):
        typ, cnt, vo = ent
        f = {1: 'B', 3: 'H', 4: 'I', 8: 'h', 9: 'i'}.get(typ)
        if not f:
            return []
        cnt = min(cnt, cap)
        if vo < 0 or vo + cnt * self.SZ[typ] > len(self.d):
            return []
        return list(struct.unpack_from('%s%d%s' % (self.e, cnt, f), self.d, vo))

    def text(self, ent):
        typ, cnt, vo = ent
        if typ != 2 or vo < 0 or vo + cnt > len(self.d):
            return ''
        return self.d[vo:vo + cnt].split(b'\0')[0].decode('latin-1')

    def info(self):
        r = {'date': '', 'need': 0, 'make': ''}
        i0 = self.ifd(self.ifd0)
        if 0x10F in i0:
            r['make'] = self.text(i0[0x10F])
        v = self.ints(i0[0x8769]) if 0x8769 in i0 else []
        if v:
            ex = self.ifd(v[0])
            if 0x9003 in ex:
                r['date'] = self.text(ex[0x9003])
        tables = [i0]
        for o in (self.ints(i0[0x14A]) if 0x14A in i0 else []):
            tables.append(self.ifd(o))
        for t in tables:
            so = self.ints(t[0x111]) if 0x111 in t else []
            sc = self.ints(t[0x117]) if 0x117 in t else []
            for x, y in zip(so, sc):
                r['need'] = max(r['need'], x + y)
            jo = self.ints(t[0x201]) if 0x201 in t else []
            jl = self.ints(t[0x202]) if 0x202 in t else []
            if jo and jl:
                r['need'] = max(r['need'], jo[0] + jl[0])
        return r


# ---------------------------------------------------------------- JPEG embebidos

SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_scan(d, s):
    """Camina un JPEG que empieza en d[s]. Devuelve (fin, ancho, alto); fin=None si esta cortado o roto."""
    n = len(d)
    i = s + 2
    w = h = 0
    while i + 4 <= n:
        if d[i] != 0xFF:
            return None, w, h
        m = d[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD9:
            return i + 2, w, h
        if m == 0x00 or m == 0x01 or 0xD0 <= m <= 0xD8:
            i += 2
            continue
        ln = (d[i + 2] << 8) | d[i + 3]
        if m in SOF and i + 9 <= n:
            h = (d[i + 5] << 8) | d[i + 6]
            w = (d[i + 7] << 8) | d[i + 8]
        i += 2 + ln
        if m == 0xDA:
            while True:
                j = d.find(b'\xff', i)
                if j < 0 or j + 1 >= n:
                    return None, w, h
                nb = d[j + 1]
                if nb == 0 or 0xD0 <= nb <= 0xD7:
                    i = j + 2
                else:
                    i = j
                    break
    return None, w, h


def find_jpegs(d):
    out = []
    pos = 0
    while True:
        s = d.find(b'\xff\xd8\xff', pos)
        if s < 0:
            break
        end, w, h = jpeg_scan(d, s)
        if end:
            out.append((s, end, w, h))
            pos = end
        else:
            pos = s + 3
    return out


def jpeg_kind(w, h):
    px = w * h
    if px >= 20000000:
        return 'full'
    if px >= 800000:
        return 'preview'
    return 'thumb'


def save_jpegs(d, base, outdir, kinds=('full', 'preview')):
    os.makedirs(outdir, exist_ok=True)
    saved, seen = [], {}
    for s, e, w, h in find_jpegs(d):
        k = jpeg_kind(w, h)
        if k not in kinds:
            continue
        seen[k] = seen.get(k, 0) + 1
        suf = k if seen[k] == 1 else '%s_%d' % (k, seen[k])
        p = os.path.join(outdir, '%s_%s.jpg' % (base, suf))
        with open(p, 'wb') as f:
            f.write(d[s:e])
        saved.append((k, s, e - s, w, h, p))
    return saved


def stamp_from(txt):
    m = re.match(r'(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)', txt or '')
    return '%s-%s-%s_%s-%s-%s' % m.groups() if m else 'SINFECHA'


# ---------------------------------------------------------------- analisis

def survey(fs, maxheads=60000):
    t0 = time.time()
    heads, st = fs.find_heads()
    say('  cadenas (cabezas): %d   [%.1fs]' % (len(heads), time.time() - t0))
    rows = []
    for k, c in enumerate(heads[:maxheads]):
        n, frags, fin = fs.stats(c)
        sig = classify(fs.rd(fs.coff(c), min(8192, fs.csize)))
        rows.append(dict(c=c, sec=fs.coff(c) // SEC, sig=sig, n=n, frags=frags, fin=fin, ref='', via='FAT'))
        if k and k % 2000 == 0:
            say('  ... %d/%d cabezas clasificadas' % (k, len(heads)))
    if len(heads) > maxheads:
        say('  AVISO: solo clasifique %d de %d cabezas' % (maxheads, len(heads)))
    return rows, st


def ref_points(fs, refdir):
    pts = {}
    for fn in sorted(os.listdir(refdir)):
        m = re.match(r'f(\d+)', fn)
        if not m:
            continue
        off = int(m.group(1)) * SEC
        if off < fs.data_off or (off - fs.data_off) % fs.csize:
            continue
        c = (off - fs.data_off) // fs.csize + 2
        if c < fs.lim:
            pts[c] = fn
    return pts


def describe_dir(fs, label, c, root=False):
    if not (2 <= c < fs.lim):
        say('  %s: cluster inicial invalido (%d)' % (label, c))
        return []
    n, frags, fin = fs.stats(c)
    head = fs.rd(fs.coff(c), min(8192, fs.csize))
    sig = classify(head)
    say('  %s: cluster %d, cadena %d cluster(es) frag=%d fin=%s, contenido=%s' % (label, c, n, frags, fin, sig))
    say('     primeros 48 bytes: %s' % head[:48].hex())
    ents = fs.dir_entries(c) if (sig == 'DIR' or root) else []
    for e in ents[:12]:
        say('     - %-16s attr=0x%02X cluster=%d tam=%d' % (ascii(e['name']), e['attr'], e['c'], e['size']))
    return ents


def table(fs, rows):
    by = {}
    for r in rows:
        by.setdefault(r['sig'], []).append(r)
    say('== CABEZAS DE CADENA POR TIPO ==')
    for sig, lst in sorted(by.items(), key=lambda kv: -sum(x['n'] for x in kv[1])):
        tot = sum(x['n'] for x in lst) * fs.csize
        fr = sum(1 for x in lst if x['frags'] > 1)
        say('  %-5s cadenas=%-6d total=%-10s fragmentadas=%d' % (sig, len(lst), human(tot), fr))


def fmt_row(fs, r):
    return '  %10d %9d %-5s %8d %9.1f %5d  %-5s %s' % (
        r['sec'], r['c'], r['sig'], r['n'], r['n'] * fs.csize / 1e6, r['frags'], r['fin'], r.get('ref', ''))


def load_photo(fs, r):
    if r['via'] == 'CARVE':
        head = fs.rd(fs.coff(r['c']), min(1 << 20, (fs.lim - r['c']) * fs.csize))
        t = Tiff(head)
        need = t.info().get('need', 0) if t.ok else 0
        n = (need + fs.csize - 1) // fs.csize if 0 < need < (300 << 20) else (64 << 20) // fs.csize
        cl = list(range(r['c'], min(r['c'] + n, fs.lim)))
        fin = 'CARVE'
    else:
        cl, fin = fs.chain(r['c'])
    data = fs.read_clusters(cl)
    info = {}
    if r['sig'] != 'JPG':
        t = Tiff(data)
        if t.ok:
            info = t.info()
    return cl, fin, data, info


def verdict(fs, sig, data, info):
    if sig == 'JPG':
        end, w, h = jpeg_scan(data, 0)
        return 'JPG_OK' if end else 'JPG_CORTADO'
    need = info.get('need', 0)
    if not need:
        return 'SIN_ESTRUCTURA'
    slack = len(data) - need
    if slack < 0:
        return 'CORTO(%dKB)' % (-slack // 1024)
    if slack < fs.csize:
        return 'OK'
    return 'LARGO(+%dKB)' % (slack // 1024)


# ---------------------------------------------------------------- comandos

def cmd_scan(fs, a):
    say('== BOOT ==')
    say('  volumen %s  (%s)' % (fs.src.path, human(fs.tot * SEC)))
    say('  cluster=%d KB  reservados=%d  FATs=%d  sectores/FAT=%d  raiz=cluster %d' % (
        fs.csize // 1024, fs.rsv, fs.nf, fs.fsz, fs.root))
    say('  datos desde sector %d, clusters de datos=%d' % (fs.data_off // SEC, fs.ncl))
    fi = fs.fsinfo_info()
    if fi:
        say('  FSInfo: libres=%d (%s)  siguiente=%d' % (fi['free'], human(fi['free'] * fs.csize), fi['nxt']))
    say('== FAT ==')
    if fs.nf > 1:
        if fs.fats[0].tobytes() == fs.fats[1].tobytes():
            say('  FAT1 == FAT2: identicas')
        else:
            diff = sum(1 for x, y in zip(fs.fats[0], fs.fats[1]) if x != y)
            say('  FAT1 vs FAT2: %d entradas distintas de %d' % (diff, len(fs.fats[0])))
    rows, st = survey(fs)
    say('  clusters: libres=%d (%s) usados=%d (%s) malos=%d  punteros invalidos=%d' % (
        st['free'], human(st['free'] * fs.csize), st['alloc'], human(st['alloc'] * fs.csize), st['bad'], st['inval']))
    say('== CARPETAS ==')
    ents = describe_dir(fs, 'RAIZ', fs.root, root=True)
    for e in ents:
        if e['attr'] & 0x10 and e['name'] in ('DCIM', 'PRIVATE', 'AVF_INFO'):
            sub = describe_dir(fs, e['name'], e['c'])
            if e['name'] == 'DCIM':
                for s in sub:
                    if s['attr'] & 0x10 and s['name'] not in ('.', '..'):
                        describe_dir(fs, 'DCIM/' + s['name'], s['c'])
    table(fs, rows)
    ph = sorted([r for r in rows if r['sig'] in PHOTO], key=lambda r: r['sec'])
    say('== FOTOS: %d cadenas con firma de foto ==' % len(ph))
    say('      sector   cluster tipo  clusters        MB  frag  fin')
    for r in ph[:90]:
        say(fmt_row(fs, r))
    if len(ph) > 90:
        say('  ... y %d mas (ver heads.csv)' % (len(ph) - 90))
    others = [r for r in rows if r['sig'] in ('DIR', 'OTRO')][:25]
    if others:
        say('== DIRECTORIOS / OTROS (muestra) ==')
        for r in others:
            say(fmt_row(fs, r))
    logdir = os.path.dirname(LOG.name) if LOG else '.'
    with open(os.path.join(logdir, 'heads.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['sector', 'cluster', 'tipo', 'clusters', 'MB', 'fragmentos', 'fin'])
        for r in sorted(rows, key=lambda r: r['sec']):
            w.writerow([r['sec'], r['c'], r['sig'], r['n'], round(r['n'] * fs.csize / 1e6, 1), r['frags'], r['fin']])
    if a.ref:
        say('== CRUCE CON PHOTOREC (%s) ==' % a.ref)
        say('  archivo                   cluster alin FAT      cabeza cadena(cl) frag fin    MB_cadena MB_photorec')
        heads = set(r['c'] for r in rows)
        k = 0
        for fn in sorted(os.listdir(a.ref)):
            m = re.match(r'f(\d+)', fn)
            if not m:
                continue
            off = int(m.group(1)) * SEC
            if off < fs.data_off:
                say('  %-24s antes del area de datos' % fn)
                continue
            rel = off - fs.data_off
            c = rel // fs.csize + 2
            if c >= fs.lim:
                continue
            n, frags, fin = fs.stats(c)
            v = fs.fat[c] & MASK
            size = os.path.getsize(os.path.join(a.ref, fn))
            k += 1
            if k <= 90:
                say('  %-24s %8d %-4s 0x%08X %-6s %9d %4d %-6s %9.1f %9.1f' % (
                    fn, c, 'si' if rel % fs.csize == 0 else 'NO', v, 'si' if c in heads else 'no', n, frags, fin,
                    n * fs.csize / 1e6, size / 1e6))
    probe = [r for r in ph if r['sig'] == 'ARW'][:a.probe]
    if probe:
        say('== SONDEO: rearmo %d ARW por cadena y comparo con la estructura TIFF del propio archivo ==' % len(probe))
        for r in probe:
            cl, fin, data, info = load_photo(fs, r)
            jp = find_jpegs(data)
            full = [j for j in jp if jpeg_kind(j[2], j[3]) == 'full']
            say('  cluster %d: %s  bytes=%d  need=%d  veredicto=%s  fecha=%s  JPEG full=%s' % (
                r['c'], human(len(data)), len(data), info.get('need', 0), verdict(fs, r['sig'], data, info),
                info.get('date', '?'), ('%dx%d' % (full[0][2], full[0][3])) if full else 'no'))


def cmd_extract(fs, a):
    want = set(t.strip().upper() for t in a.types.split(','))
    say('== EXTRACCION == salida: %s' % a.out)
    rows, st = survey(fs)
    sel = [r for r in rows if r['sig'] in want and r['n'] * fs.csize >= a.min_mb * 1e6]
    say('  cadenas de foto en la FAT: %d' % len(sel))
    if a.ref:
        pts = ref_points(fs, a.ref)
        known = set(r['c'] for r in rows)
        extra = 0
        for c, fn in sorted(pts.items()):
            if c in known:
                continue
            sig = classify(fs.rd(fs.coff(c), min(8192, fs.csize)))
            if sig in want:
                n, frags, fin = fs.stats(c)
                free = (fs.fat[c] & MASK) == 0
                sel.append(dict(c=c, sec=fs.coff(c) // SEC, sig=sig, n=n, frags=frags, fin=fin, ref=fn,
                                via='CARVE' if free else 'FAT'))
                extra += 1
        say('  inicios extra desde PhotoRec que no son cabeza de cadena: %d' % extra)
    sel.sort(key=lambda r: r['sec'])
    if a.limit:
        sel = sel[:a.limit]
    os.makedirs(a.out, exist_ok=True)
    jdir = os.path.join(a.out, 'JPEG_embebidos')
    rep = []
    t0 = time.time()
    for k, r in enumerate(sel, 1):
        cl, fin, data, info = load_photo(fs, r)
        sig = r['sig']
        ext = {'ARW': 'ARW', 'NEF': 'NEF', 'CR2': 'CR2', 'JPG': 'JPG'}.get(sig, 'TIF')
        base = '%s_c%d' % (stamp_from(info.get('date')), r['c'])
        sub = os.path.join(a.out, ext)
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, base + '.' + ext)
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                f.write(data)
        jp = save_jpegs(data, base, jdir) if sig != 'JPG' else []
        full = [j for j in jp if j[0] == 'full']
        ver = verdict(fs, sig, data, info)
        frags = 1 + sum(1 for i in range(1, len(cl)) if cl[i] != cl[i - 1] + 1)
        rep.append([os.path.relpath(path, a.out), r['c'], r['sec'], len(cl), frags, fin, r['via'], len(data),
                    info.get('date', ''), info.get('need', 0), ver,
                    ('%dx%d' % (full[0][3], full[0][4])) if full else ''])
        say('  [%d/%d] c=%d %s %s frag=%d %s %s -> %s  %s' % (
            k, len(sel), r['c'], sig, human(len(data)), frags, fin, r['via'], os.path.relpath(path, a.out), ver))
    with open(os.path.join(a.out, 'reporte.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['archivo', 'cluster', 'sector', 'clusters', 'fragmentos', 'fin_cadena', 'via', 'bytes',
                    'fecha_exif', 'tamano_segun_TIFF', 'veredicto', 'jpeg_full'])
        w.writerows(rep)
    ok = sum(1 for x in rep if x[10] in ('OK', 'JPG_OK'))
    say('== LISTO: %d archivos, %d con estructura completa (OK), %.0fs ==' % (len(rep), ok, time.time() - t0))
    say('  fotos:    %s' % a.out)
    say('  reporte:  %s' % os.path.join(a.out, 'reporte.csv'))


def cmd_image(fs, a):
    total = fs.tot * SEC
    say('== IMAGEN == %s -> %s (%s)' % (fs.src.path, a.out, human(total)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    h = hashlib.sha256()
    t0 = time.time()
    done, nxt = 0, 0
    with open(a.out, 'wb') as f:
        while done < total:
            b = fs.rd(done, min(4 << 20, total - done))
            f.write(b)
            h.update(b)
            done += len(b)
            if done >= nxt:
                el = max(time.time() - t0, 1e-6)
                say('  %6.0f MB de %.0f MB  (%.0f MB/s, faltan ~%.0f s)' % (
                    done / 1e6, total / 1e6, done / 1e6 / el, (total - done) / (done / el)))
                nxt += 256 << 20
    say('  LISTO: %s  sha256=%s  sectores_ilegibles=%d  %.0fs' % (human(done), h.hexdigest(), fs.src.bad_sectors,
                                                                  time.time() - t0))


def cmd_thumbs(a):
    files = []
    for p in a.paths:
        if os.path.isdir(p):
            for root, _, fns in os.walk(p):
                for fn in fns:
                    if fn.lower().endswith(('.sr2', '.arw', '.srf', '.nef', '.cr2', '.dng', '.tif', '.tiff')):
                        files.append(os.path.join(root, fn))
        else:
            files.append(p)
    kinds = ('full', 'preview', 'thumb') if a.all else ('full', 'preview')
    for p in sorted(files):
        with open(p, 'rb') as f:
            d = f.read()
        base = os.path.splitext(os.path.basename(p))[0]
        t = Tiff(d)
        info = t.info() if t.ok else {}
        say('%s  (%s)  fecha=%s  tamano_segun_TIFF=%s' % (p, human(len(d)), info.get('date', '?'), info.get('need', '?')))
        for k, s, n, w, h, pth in save_jpegs(d, base, a.out, kinds):
            say('    %-8s %dx%d  %d KB  en offset %d -> %s' % (k, w, h, n // 1024, s, pth))


def main():
    global LOG
    ap = argparse.ArgumentParser(description='Rescate de fotos RAW de una SD FAT32 danada (solo lectura).')
    sp = ap.add_subparsers(dest='cmd', required=True)
    for name in ('scan', 'extract', 'image'):
        s = sp.add_parser(name)
        s.add_argument('src', help='letra de unidad (E:) o archivo imagen')
        s.add_argument('--ref', help='carpeta de PhotoRec (archivos fNNNN.*) para sumar inicios de foto')
        s.add_argument('--fat', type=int, default=1, help='que copia de la FAT usar (1 o 2)')
        s.add_argument('--log')
        s.add_argument('--pause', action='store_true')
        if name == 'scan':
            s.add_argument('--probe', type=int, default=6, help='cuantas ARW rearmar para verificar')
        elif name == 'extract':
            s.add_argument('--out', default='SALIDA')
            s.add_argument('--types', default='ARW,NEF,CR2,TIFF,JPG')
            s.add_argument('--min-mb', type=float, default=3.0)
            s.add_argument('--limit', type=int, default=0)
        else:
            s.add_argument('--out', default='tarjeta.img')
    t = sp.add_parser('thumbs')
    t.add_argument('paths', nargs='+')
    t.add_argument('--out', default='THUMBS')
    t.add_argument('--all', action='store_true', help='incluir miniaturas chicas')
    t.add_argument('--log')
    t.add_argument('--pause', action='store_true')
    a = ap.parse_args()
    logpath = a.log or os.path.join('logs', a.cmd + '.txt')
    os.makedirs(os.path.dirname(os.path.abspath(logpath)), exist_ok=True)
    LOG = open(logpath, 'w', encoding='utf-8', errors='replace')
    code = 0
    try:
        if a.cmd == 'thumbs':
            cmd_thumbs(a)
        else:
            src = Source(a.src)
            fs = Fat32(src, a.fat)
            {'scan': cmd_scan, 'extract': cmd_extract, 'image': cmd_image}[a.cmd](fs, a)
            if src.bad_sectors:
                say('!! sectores ilegibles: %d (rellenados con ceros)' % src.bad_sectors)
    except SystemExit as ex:
        say('ERROR: %s' % ex)
        code = 1
    except Exception:
        say(traceback.format_exc())
        code = 2
    say('== FIN (codigo %d) ==' % code)
    if a.pause:
        input('Enter para cerrar...')
    return code


if __name__ == '__main__':
    sys.exit(main())
