#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ntfs_formateado.py - recuperar un disco NTFS formateado (formato rapido) usando el indice (MFT) anterior. SOLO LECTURA.

RUN es la carpeta del rescate (sd-raw-rescue\\AAAA-MM-DD). El origen nunca se escribe.

  python ntfs_formateado.py diagnostico ORIGEN --log RUN\\logs\\diagnostico.txt      (elevado)
      cuanto dato queda, rastros de otro sistema y si sobrevivieron registros con nombre
  python rescatar_sd.py image F: --size 4294967296 --out RUN\\imagen\\inicio.img     (elevado; 4 GB del principio)
  python ntfs_formateado.py inventario RUN\\imagen\\inicio.img RUN
      archivos, carpetas, fechas y ubicacion segun el indice viejo, y cuales piso el sistema nuevo
  python ntfs_formateado.py copiar F: RUN --tanda tanda1 [--sin CARPETA] [--solo CARPETA] [--simular]   (elevado)
      copia directa en el orden del disco; se puede cortar y volver a correr
  python ntfs_formateado.py verificar RUN tanda1        (necesita pillow y numpy)
  python ntfs_formateado.py entrega RUN                 REPORTE.csv, LEEME.txt, SHA256.txt y cierre de cuentas

diagnostico, inventario, copiar y entrega usan solo la biblioteca estandar. ORIGEN es el volumen (F:), un disco
(\\\\.\\PhysicalDriveN, solo en diagnostico) o un archivo imagen del volumen.
"""
import argparse
import calendar
import collections
import csv
import ctypes
import datetime
import glob
import hashlib
import io
import json
import mmap
import os
import re
import shutil
import struct
import sys
import time
import traceback
import zipfile
import xml.dom.minidom
from concurrent.futures import ProcessPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import rescatar_sd as R  # noqa: E402

TROZO = 8 << 20
BASURA = ('system volume information\\', '.fseventsd\\')
CERO = bytes(65536)
LOG = None


def say(m=''):
    m = str(m)
    try:
        print(m, flush=True)
    except UnicodeError:
        print(m.encode('ascii', 'replace').decode('ascii'), flush=True)
    if LOG:
        LOG.write('%s  %s\n' % (time.strftime('%H:%M:%S'), m))
        LOG.flush()


def abrir_log(path):
    global LOG
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    LOG = open(path, 'a', encoding='utf-8')


def largo_win(p):
    return '\\\\?\\' + os.path.abspath(p) if os.name == 'nt' else os.path.abspath(p)


def leer_csv(p):
    with open(p, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f, delimiter=';'))


# ---------------------------------------------------------------- NTFS

def geometria(b):
    """Del sector de arranque NTFS: bytes por cluster, LCN de la MFT, bytes por registro y serie. None si no es NTFS."""
    if b[3:11] != b'NTFS    ':
        return None
    bps, spc = struct.unpack_from('<H', b, 11)[0], b[13]
    if spc > 128:
        spc = 1 << (256 - spc)
    cl = bps * spc
    c = struct.unpack_from('<b', b, 64)[0]
    return dict(cluster=cl, mft=struct.unpack_from('<Q', b, 48)[0], registro=c * cl if c > 0 else 1 << -c,
                serie=b[72:80].hex())


def fecha(v):
    try:
        return (datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=v // 10)).strftime('%Y-%m-%d %H:%M:%S')
    except (OverflowError, ValueError):
        return ''


def runs(b):
    """Runlist -> [[lcn, clusters]]; lcn None = hueco."""
    out, i, lcn = [], 0, 0
    while i < len(b) and b[i]:
        nl, no = b[i] & 15, b[i] >> 4
        i += 1
        n = int.from_bytes(b[i:i + nl], 'little')
        i += nl
        if no:
            lcn += int.from_bytes(b[i:i + no], 'little', signed=True)
            out.append([lcn, n])
        else:
            out.append([None, n])
        i += no
    return out


def parsear(raw):
    """Un registro FILE (1 o 4 KB) o None si esta roto (la secuencia de actualizacion no cierra)."""
    r = bytearray(raw)
    size = len(r)
    if size < 512 or r[:4] != b'FILE':
        return None
    usa, n = struct.unpack_from('<HH', r, 4)
    if n != size // 512 + 1 or usa + 2 * n > size:
        return None
    for i in range(1, n):
        if r[i * 512 - 2:i * 512] != r[usa:usa + 2]:
            return None
        r[i * 512 - 2:i * 512] = r[usa + 2 * i:usa + 2 * i + 2]
    seq, = struct.unpack_from('<H', r, 16)
    p, flags = struct.unpack_from('<HH', r, 20)
    rec = dict(num=struct.unpack_from('<I', r, 44)[0], seq=seq, base=struct.unpack_from('<Q', r, 32)[0] & 0xFFFFFFFFFFFF,
               uso=bool(flags & 1), dir=bool(flags & 2), nombres=[], datos=[], tam=None, residente=None, mod='',
               cifrado=False, comprimido=False)
    try:
        while p + 24 <= size:
            t, ln = struct.unpack_from('<II', r, p)
            if t == 0xFFFFFFFF or ln < 24 or p + ln > size:
                break
            nores, nlen = r[p + 8], r[p + 9]
            aflags, = struct.unpack_from('<H', r, p + 12)
            if not nores:
                clen, coff = struct.unpack_from('<IH', r, p + 16)
                c = bytes(r[p + coff:p + coff + clen])
                if t == 0x10 and len(c) >= 16:
                    rec['mod'] = fecha(struct.unpack_from('<Q', c, 8)[0])
                elif t == 0x30 and len(c) >= 66:
                    rec['nombres'].append((c[65], c[66:66 + 2 * c[64]].decode('utf-16-le', 'replace'),
                                           int.from_bytes(c[:6], 'little'), int.from_bytes(c[6:8], 'little')))
                elif t == 0x80 and nlen == 0:
                    rec['residente'], rec['tam'] = c.hex(), len(c)
            elif t == 0x80 and nlen == 0:
                vcn, = struct.unpack_from('<Q', r, p + 16)
                roff, = struct.unpack_from('<H', r, p + 32)
                if vcn == 0:
                    rec['tam'], = struct.unpack_from('<Q', r, p + 48)
                rec['datos'].append((vcn, runs(bytes(r[p + roff:p + ln]))))
                rec['comprimido'] |= bool(aflags & 0x00FF)
                rec['cifrado'] |= bool(aflags & 0x4000)
            p += ln
    except (struct.error, IndexError):
        return None
    return rec


def mejor(x):
    return sorted(x['nombres'], key=lambda n: n[0] == 2)[0]        # el nombre largo antes que el 8.3


def ruta(x, recs):
    partes, visto = [], set()
    while True:
        _, nom, padre, pseq = mejor(x)
        partes.append(nom)
        if padre == 5:
            break
        p = recs.get(padre)
        if padre in visto or not p or not p['nombres'] or not p['dir'] or p['seq'] not in (pseq, pseq + 1):
            partes.append('CARPETA_SIN_NOMBRE_%d' % padre)         # el registro de la carpeta se piso
            break
        visto.add(padre)
        x = p
    return '\\'.join(reversed(partes))


def clusters_pisados(m, g, archivos):
    """Marca cuantos clusters de cada archivo viejo ocupa hoy el sistema nuevo (su $Bitmap, registro 6)."""
    cl, rs = g['cluster'], g['registro']
    pos = g['mft'] * cl + 6 * rs
    rec = parsear(m[pos:pos + rs]) if pos + rs <= len(m) else None
    if not rec or not rec['nombres'] or mejor(rec)[1] != '$Bitmap':
        return 'no pude leer el $Bitmap nuevo (fuera de la copia o ilegible)'
    bm = bytearray()
    for lcn, n in rec['datos'][0][1]:
        if (lcn + n) * cl > len(m):
            return 'el $Bitmap nuevo esta fuera de la copia: copiar mas del principio'
        bm += m[lcn * cl:(lcn + n) * cl]
    bm = bm[:rec['tam']]
    for a in archivos:
        tocados = 0
        for lcn, n in a['runs']:
            if lcn is None:
                continue
            k, fin = lcn, min(lcn + n, len(bm) * 8)
            while k < fin:
                byte = bm[k >> 3]
                if not k & 7 and fin - k >= 8 and byte in (0, 255):
                    tocados += 8 if byte else 0
                    k += 8
                else:
                    tocados += byte >> (k & 7) & 1
                    k += 1
        a['pisados'] = tocados
    return ''


def cmd_inventario(a):
    with open(a.copia, 'rb') as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    g = geometria(m[:512])
    if not g:
        raise SystemExit('La copia no empieza con un sector NTFS: tiene que ser del volumen (F:), no del disco entero.')
    cl, rs = g['cluster'], g['registro']
    recs, ultimo, i = {}, 0, m.find(b'FILE')
    while i >= 0:
        if i % rs == 0:
            x = parsear(m[i:i + rs])
            if x:
                ant = recs.get(x['num'])
                if ant is None or (not ant['nombres'] and x['nombres']):
                    recs[x['num']] = x
                ultimo = i
        i = m.find(b'FILE', i + 4)
    for x in list(recs.values()):                                   # registros de extension: suman al registro base
        if x['base'] and x['base'] in recs and x['base'] != x['num']:
            b = recs[x['base']]
            b['datos'] += x['datos']
            b['nombres'] += x['nombres']
            if x['tam'] is not None and (b['tam'] is None or any(v == 0 for v, _ in x['datos'])):
                b['tam'] = x['tam']
    archivos = []
    for x in recs.values():
        if x['base'] or x['dir'] or not x['nombres'] or mejor(x)[1].startswith('$') or x['num'] < 24:
            continue
        rl = [r for _, lst in sorted(x['datos'], key=lambda d: d[0]) for r in lst]
        if x['residente'] is not None:
            est = 'en_el_indice'
        elif x['tam'] is None or not rl:
            est = 'sin_ubicacion'
        elif sum(n for _, n in rl) * cl < x['tam']:
            est = 'ubicacion_incompleta'
        else:
            est = 'ubicacion_completa'
        archivos.append(dict(num=x['num'], ruta=ruta(x, recs), tam=x['tam'] or 0, mod=x['mod'], uso=x['uso'], estado=est,
                             runs=rl, residente=x['residente'], comprimido=x['comprimido'], cifrado=x['cifrado'],
                             pisados=0, comparte=''))
    if not archivos:
        raise SystemExit('No quedaron registros de archivos en la copia: copiar mas del principio o recuperar por firmas.')
    archivos.sort(key=lambda z: z['ruta'].lower())
    aviso_bm = clusters_pisados(m, g, archivos)
    tope, duenio = 0, None
    for ini, fin, k in sorted((l, l + n, k) for k, z in enumerate(archivos) for l, n in z['runs'] if l is not None):
        if ini < tope and k != duenio:
            archivos[k]['comparte'] = archivos[duenio]['ruta']
            archivos[duenio]['comparte'] = archivos[k]['ruta']
        if fin > tope:
            tope, duenio = fin, k
    trab = os.path.join(a.run, 'trabajo')
    os.makedirs(trab, exist_ok=True)
    with open(os.path.join(trab, 'inventario.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(meta=g, archivos=archivos), f)
    with open(os.path.join(trab, 'inventario.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['ruta', 'bytes', 'modificado_utc', 'estado', 'borrado_antes', 'tramos', 'clusters_pisados', 'comparte_con'])
        for z in archivos:
            w.writerow([z['ruta'], z['tam'], z['mod'], z['estado'], int(not z['uso']), len(z['runs']), z['pisados'], z['comparte']])

    say('registros validos: %d | el ultimo en el byte %d de una copia de %d' % (len(recs), ultimo, len(m)))
    if ultimo > len(m) - (256 << 20):
        say('!! hay registros pegados al final de la copia: el indice puede seguir. Copiar mas y repetir.')
    say('archivos: %d | %.1f GB | borrados antes del formateo: %d' % (
        len(archivos), sum(z['tam'] for z in archivos) / 1e9, sum(not z['uso'] for z in archivos)))
    say('estado: %s' % dict(collections.Counter(z['estado'] for z in archivos)))
    say('fragmentados: %d | comprimidos: %d | cifrados: %d | comparten espacio: %d' % (
        sum(len(z['runs']) > 1 for z in archivos), sum(z['comprimido'] for z in archivos),
        sum(z['cifrado'] for z in archivos), sum(bool(z['comparte']) for z in archivos)))
    say('pisados por el sistema nuevo: %s' % (aviso_bm or sum(bool(z['pisados']) for z in archivos)))
    grupos = collections.defaultdict(lambda: [0, 0])
    for z in archivos:
        e = grupos['\\'.join(z['ruta'].split('\\')[:-1][:2]) or '(raiz)']
        e[0] += 1
        e[1] += z['tam']
    for k, (n, t) in sorted(grupos.items(), key=lambda kv: -kv[1][1])[:60]:
        say('  %8.1f GB %7d arch  %s' % (t / 1e9, n, k))


# ---------------------------------------------------------------- diagnostico

def firma(b):
    for ini, nom in ((b'\xff\xd8\xff', 'JPG'), (b'II*\0', 'TIFF/RAW'), (b'MM\0*', 'TIFF/RAW'), (b'FILE0', 'registro NTFS'),
                     (b'\x89PNG', 'PNG'), (b'%PDF', 'PDF'), (b'PK\x03\x04', 'ZIP/Office'), (b'8BPS', 'PSD')):
        if b.startswith(ini):
            return nom
    return 'video/HEIC' if b[4:8] == b'ftyp' else None


def cmd_diagnostico(a):
    src = R.Source(a.origen)
    total = src.length()
    if not total:
        raise SystemExit('No pude saber el tamano del origen.')
    say('== DIAGNOSTICO (solo lectura) %s  %d bytes' % (a.origen, total))
    s0 = src.read(0, 1024)
    base, g = 0, geometria(s0)
    if not g and s0[510:512] == b'\x55\xaa':
        for i in range(4):
            ent = s0[446 + 16 * i:462 + 16 * i]
            if ent[4] == 0x07:
                cand = struct.unpack_from('<I', ent, 8)[0] * 512
                g = geometria(src.read(cand, 512))
                if g:
                    base = cand
                    break
    say('GPT: %s | NTFS actual: %s' % (s0[512:520] == b'EFI PART', dict(g, desde_byte=base) if g else 'no'))
    say('== rastros de sistemas de archivos en los primeros 700 MB')
    hall = collections.defaultdict(list)
    for off in range(0, min(700 << 20, total), TROZO):
        d = src.read(off, TROZO)
        for sig, delta, nom in ((b'EXFAT   ', 3, 'exFAT'), (b'NTFS    ', 3, 'NTFS'), (b'FAT32   ', 82, 'FAT32'),
                                (b'H+\x00\x04', 1024, 'HFS+'), (b'HX\x00\x05', 1024, 'HFSX'), (b'NXSB', 32, 'APFS')):
            i = d.find(sig)
            while i >= 0:
                pos = off + i - delta
                if pos >= 0 and pos % 512 == 0:
                    hall[nom].append(pos)
                i = d.find(sig, i + 1)
    for k, v in hall.items():
        say('  %-6s %d, en %s' % (k, len(v), v[:6]))
    say('== muestreo de todo el origen')
    n, tam = a.muestras, 64 << 10
    zonas = [[0, 0] for _ in range(20)]
    tipos, con = collections.Counter(), 0
    for k in range(n):
        off = max(0, total - tam) * k // max(1, n - 1) // 4096 * 4096
        d = src.read(off, tam)
        z = zonas[min(19, off * 20 // total)]
        z[1] += 1
        if d.count(0) != len(d):
            con += 1
            z[0] += 1
            for j in range(0, len(d), 4096):
                f = firma(d[j:j + 16])
                if f:
                    tipos[f] += 1
        if k and k % 1000 == 0:
            say('  %d/%d' % (k, n))
    say('  muestras con datos: %.1f %% -> unos %.0f GB' % (100 * con / n, total * con / n / 1e9))
    say('  por zona (%% con datos, 20 zonas): %s' % ' '.join('%d' % (100 * x / max(1, y)) for x, y in zonas))
    say('  cabeceras vistas: %s' % dict(tipos.most_common()))
    if g:
        ini = base + g['mft'] * g['cluster']
        rs, cuenta, nombres = g['registro'], 0, []
        say('== registros NTFS con nombre en el primer GB desde la MFT')
        for off in range(ini, min(ini + (1 << 30), total), TROZO):
            d = src.read(off, TROZO)
            for j in range(0, len(d), rs):
                x = parsear(d[j:j + rs]) if d[j:j + 4] == b'FILE' else None
                if x and x['nombres'] and not mejor(x)[1].startswith('$'):
                    cuenta += 1
                    if len(nombres) < 15:
                        nombres.append(mejor(x)[1])
        say('  %d registros | ejemplos: %s' % (cuenta, ', '.join(nombres)))
    say('sectores ilegibles: %d' % src.bad_sectors)


# ---------------------------------------------------------------- copiar

def limpio(parte):
    parte = 'PAPELERA' if parte.upper() == '$RECYCLE.BIN' else parte
    parte = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', parte).rstrip(' .') or '_'
    return '_' + parte if re.fullmatch(r'(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?', parte) else parte


def cargar_inventario(run):
    p = os.path.join(run, 'trabajo', 'inventario.json')
    if not os.path.exists(p):
        raise SystemExit('Falta %s: correr antes "inventario".' % p)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def es_basura(r):
    r = r.lower()
    return r.startswith(BASURA) or r.endswith('desktop.ini')


def elegidos(inv, sin, solo, tope):
    usados, out = set(), []
    for x in inv['archivos']:
        r = x['ruta'].lower()
        if es_basura(r):
            continue
        if solo and not any(r.startswith(p.lower() + '\\') for p in solo):
            continue
        if any(r.startswith(p.lower() + '\\') for p in sin):
            continue
        if tope and any(l is None or (l + n) * inv['meta']['cluster'] > tope for l, n in x['runs']):
            continue
        rel = '\\'.join(limpio(p) for p in x['ruta'].split('\\'))
        if rel.lower() in usados:
            base, ext = os.path.splitext(rel)
            rel = '%s__%d%s' % (base, x['num'], ext)
        usados.add(rel.lower())
        out.append(dict(x, rel=rel))
    out.sort(key=lambda x: x['runs'][0][0] if x['runs'] and x['runs'][0][0] is not None else -1)   # orden del disco
    return out


def trozos(src, x, cl):
    if x['residente'] is not None:
        yield bytes.fromhex(x['residente'])
        return
    falta = x['tam']
    for lcn, n in x['runs']:
        largo, hecho = min(n * cl, falta), 0
        while hecho < largo:
            k = min(TROZO, largo - hecho)
            yield bytes(k) if lcn is None else src.read(lcn * cl + hecho, k)
            hecho += k
        falta -= largo
        if not falta:
            return


def control(ext, ini, fin, todo_cero):
    """Control rapido mientras se copia. La revision completa es 'verificar'."""
    if todo_cero:
        return 'VACIO'
    firmas = {'.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',), '.arw': (b'II*\0',), '.cr2': (b'II*\0',),
              '.nef': (b'MM\0*', b'II*\0'), '.zip': (b'PK',)}
    if ext in firmas:
        if not ini.startswith(firmas[ext]):
            return 'CABECERA_MAL'
        return 'SIN_FIN_JPG' if ext in ('.jpg', '.jpeg') and b'\xff\xd9' not in fin else 'CABECERA_OK'
    if ext in ('.mp4', '.mov'):
        return 'CABECERA_OK' if ini[4:8] in (b'ftyp', b'moov', b'mdat', b'wide') else 'CABECERA_MAL'
    return 'SIN_CONTROL'


def cmd_copiar(a):
    run = os.path.abspath(a.run)
    abrir_log(os.path.join(run, 'logs', 'copia_%s.txt' % a.tanda))
    inv = cargar_inventario(run)
    cl = inv['meta']['cluster']
    lista = elegidos(inv, a.sin, a.solo, a.tope_origen)
    total = sum(x['tam'] for x in lista)
    say('== TANDA %s: %d archivos, %.1f GB (%.1f GiB) | libre en destino %.1f GiB' % (
        a.tanda, len(lista), total / 1e9, total / 2 ** 30, shutil.disk_usage(run).free / 2 ** 30))
    raros = sum(x['comprimido'] or x['cifrado'] for x in lista)
    if raros:
        say('!! %d archivos comprimidos o cifrados por NTFS: salen tal cual estan en el disco, no van a abrir.' % raros)
    if a.simular:
        g = collections.Counter()
        for x in lista:
            g[x['rel'].split('\\')[0] if '\\' in x['rel'] else '(raiz)'] += x['tam']
        for k, v in g.most_common():
            say('   %8.1f GB  %s' % (v / 1e9, k))
        return
    origen = a.origen.rstrip('\\')
    volumen = re.fullmatch(r'[A-Za-z]:', origen)
    if os.path.splitdrive(run)[0].upper() == (origen.upper() if volumen else None):
        raise SystemExit('El destino esta en el disco de origen: no sigo.')
    src = R.Source(origen)
    actual = geometria(src.read(0, 512))
    if not actual or actual['serie'] != inv['meta']['serie']:
        raise SystemExit('El origen no es el volumen del inventario (la serie NTFS no coincide). No sigo.')
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)      # que la PC no se suspenda
    destino = os.path.join(run, 'RECUPERADO_FINAL')
    rep_path = os.path.join(run, 'trabajo', 'copia_%s.csv' % a.tanda)
    nuevo = not os.path.exists(rep_path)
    cuenta, hecho, copiado, t0, aviso = collections.Counter(), 0, 0, time.time(), 0
    reserva = int(a.reserva_gib * 2 ** 30)
    with open(rep_path, 'a', newline='', encoding='utf-8-sig' if nuevo else 'utf-8') as rep:
        w = csv.writer(rep, delimiter=';')
        if nuevo:
            w.writerow(['archivo', 'bytes', 'control', 'sectores_ilegibles', 'comparte_con', 'sha256', 'modificado_utc',
                        'clusters_pisados'])
        for i, x in enumerate(lista, 1):
            dst = largo_win(os.path.join(destino, x['rel']))
            if os.path.exists(dst) and os.path.getsize(dst) == x['tam']:
                cuenta['ya_estaba'] += 1
                hecho += x['tam']
                continue
            if shutil.disk_usage(run).free < x['tam'] + reserva:
                raise SystemExit('PARO: quedan menos de %g GiB libres. Liberar lugar y volver a correr.' % a.reserva_gib)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            malos, h = src.bad_sectors, hashlib.sha256()
            ini, fin, cero, n = b'', b'', True, 0
            with open(dst + '.parcial', 'wb') as f:
                for b in trozos(src, x, cl):
                    f.write(b)
                    h.update(b)
                    if not n:
                        ini = b[:16]
                    fin = (fin + b[-65536:])[-65536:]
                    cero = cero and b.count(0) == len(b)
                    n += len(b)
            if n != x['tam']:
                cuenta['LARGO_MAL'] += 1
                say('!! largo distinto en %s: %d de %d' % (x['rel'], n, x['tam']))
                continue
            os.replace(dst + '.parcial', dst)
            try:
                ep = calendar.timegm(time.strptime(x['mod'], '%Y-%m-%d %H:%M:%S'))
                os.utime(dst, (ep, ep))
            except (ValueError, OverflowError, OSError):
                pass
            est = control(os.path.splitext(x['rel'])[1].lower(), ini, fin, cero and n > 0)
            cuenta[est] += 1
            w.writerow([x['rel'], n, est, src.bad_sectors - malos, x['comparte'], h.hexdigest(), x['mod'], x['pisados']])
            rep.flush()
            hecho += n
            copiado += n
            if time.time() - aviso > 20 or i == len(lista):
                aviso = time.time()
                vel = copiado / max(1e-6, aviso - t0)
                say('  %d/%d | %.1f de %.1f GB | %.0f MB/s | faltan ~%.0f min | ilegibles %d | %s' % (
                    i, len(lista), hecho / 1e9, total / 1e9, vel / 1e6, (total - hecho) / max(1, vel) / 60,
                    src.bad_sectors, dict(cuenta)))
    say('LISTO: %s | sectores ilegibles %d | %.0f min' % (dict(cuenta), src.bad_sectors, (time.time() - t0) / 60))


# ---------------------------------------------------------------- verificar

def atomos(d):
    p, vistos = 0, []
    while p + 8 <= len(d):
        n, t = struct.unpack_from('>I4s', d, p)
        if n == 1:
            n = struct.unpack_from('>Q', d, p + 8)[0]
        elif n == 0:
            n = len(d) - p
        if n < 8 or p + n > len(d):
            return False, vistos
        vistos.append(t.decode('latin-1'))
        p += n
    return p == len(d), vistos


def huecos(d, ini, fin):
    """Bloques de 64 KB enteros en cero dentro de d[ini:fin]."""
    a = (ini + 65535) // 65536 * 65536
    return sum(d[p:p + 65536] == CERO for p in range(a, fin - 65535, 65536))


def revisar(arg):
    import mmap as _mm
    base, rel, tam = arg
    ext = os.path.splitext(rel)[1].lower()
    d = None
    try:
        with open(largo_win(os.path.join(base, rel)), 'rb') as f:
            real = os.fstat(f.fileno()).st_size
            if real != tam:
                return rel, 'MAL', 'tamano %d, esperado %d' % (real, tam)
            if ext in ('.mp4', '.mov') and tam:                     # videos grandes: sin cargarlos en memoria
                d = _mm.mmap(f.fileno(), 0, access=_mm.ACCESS_READ)
                ok, v = atomos(d)
                return rel, 'OK_ESTRUCTURA' if ok and 'moov' in v else 'MAL', ' '.join(v[:6])
            if ext not in ('.jpg', '.jpeg', '.arw', '.nef', '.cr2', '.dng', '.zip', '.xml'):
                return rel, 'SIN_CONTROL', ''
            d = f.read()
        if ext in ('.jpg', '.jpeg'):
            import exfat_jpg as E
            ok, msg = E.decodifica(d)
            return rel, 'OK_DECODIFICA' if ok else 'MAL', msg
        if ext in ('.arw', '.nef', '.cr2', '.dng'):
            t = R.Tiff(d)
            if not t.ok:
                return rel, 'MAL', 'sin estructura TIFF'
            need = t.info()['need']
            if not need or need > len(d):
                return rel, 'MAL', 'la estructura pide %d bytes y hay %d' % (need, len(d))
            h = huecos(d, 1 << 20, need)
            if h:
                return rel, 'REVISAR', '%d bloques de 64 KB en blanco' % h
            js = R.find_jpegs(d[:16 << 20])
            if not js:
                return rel, 'REVISAR', 'sin imagen embebida'
            import exfat_jpg as E
            s, e, _, _ = max(js, key=lambda j: j[2] * j[3])
            ok, msg = E.decodifica(d[s:e])
            if not ok:
                return rel, 'REVISAR', 'imagen embebida rota: ' + msg
            if need < len(d) * 0.9:
                return rel, 'REVISAR', 'la estructura usa %d de %d bytes' % (need, len(d))
            return rel, 'OK_ESTRUCTURA', ''
        if ext == '.zip':
            malo = zipfile.ZipFile(io.BytesIO(d)).testzip()
            return rel, 'MAL' if malo else 'OK_DECODIFICA', malo or ''
        xml.dom.minidom.parseString(d)
        return rel, 'OK_DECODIFICA', ''
    except Exception as ex:
        return rel, 'MAL', str(ex)[:120]


def cmd_verificar(a):
    try:
        import exfat_jpg  # noqa: F401
    except ImportError as ex:
        raise SystemExit('verificar necesita numpy y pillow (%s)' % ex)
    base = os.path.join(a.run, 'RECUPERADO_FINAL')
    filas = leer_csv(os.path.join(a.run, 'trabajo', 'copia_%s.csv' % a.tanda))
    args = [(base, f['archivo'], int(f['bytes'])) for f in filas]
    res = []
    with ProcessPoolExecutor(min(8, os.cpu_count() or 4)) as ex:
        for k, r in enumerate(ex.map(revisar, args, chunksize=4), 1):
            res.append(r)
            if k % 2000 == 0:
                say('  %d/%d' % (k, len(args)))
    with open(os.path.join(a.run, 'trabajo', 'verificacion_%s.csv' % a.tanda), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['archivo', 'resultado', 'detalle'])
        w.writerows(res)
    say('revisados: %d' % len(res))
    for (e, s), n in sorted(collections.Counter((os.path.splitext(r[0])[1].lower(), r[1]) for r in res).items()):
        say('  %-8s %-14s %6d' % (e or '(sin)', s, n))
    malos = [r for r in res if r[1] in ('MAL', 'REVISAR')]
    say('con problema: %d' % len(malos))
    for r in malos[:40]:
        say('  %s | %s | %s' % (r[1], r[0], r[2]))


# ---------------------------------------------------------------- entrega

def cmd_entrega(a):
    final, trab = os.path.join(a.run, 'RECUPERADO_FINAL'), os.path.join(a.run, 'trabajo')
    inv = cargar_inventario(a.run)
    copia, ver = {}, {}
    for p in sorted(glob.glob(os.path.join(glob.escape(trab), 'copia_*.csv'))):
        copia.update({f['archivo']: f for f in leer_csv(p)})
    for p in sorted(glob.glob(os.path.join(glob.escape(trab), 'verificacion_*.csv'))):
        ver.update({f['archivo']: f for f in leer_csv(p)})
    if not copia:
        raise SystemExit('No hay ninguna tanda copiada.')
    raiz_larga = largo_win(final)
    disco = {}
    for raiz, _, fns in os.walk(raiz_larga):
        for fn in fns:
            p = os.path.join(raiz, fn)
            disco[os.path.relpath(p, raiz_larga)] = os.path.getsize(p)
    for n in ('REPORTE.csv', 'LEEME.txt', 'SHA256.txt'):
        disco.pop(n, None)
    faltan = [x for x in copia if x not in disco]
    sobran = [x for x in disco if x not in copia]
    mal_tam = [x for x in copia if x in disco and disco[x] != int(copia[x]['bytes'])]
    basura = sum(es_basura(x['ruta']) for x in inv['archivos'])
    pendientes = len(inv['archivos']) - basura - len(copia)
    say('indice: %d | copiados: %d | de sistema (afuera): %d | SIN COPIAR: %d' % (
        len(inv['archivos']), len(copia), basura, pendientes))
    say('en disco: %d | faltan %d | sobran %d | tamano distinto %d | sin revisar %d' % (
        len(disco), len(faltan), len(sobran), len(mal_tam), len(set(copia) - set(ver))))
    filas = []
    for nom, c in sorted(copia.items()):
        v = ver.get(nom)
        pis = int(c.get('clusters_pisados') or 0)
        estado, det = 'COMPLETA', ''
        if nom in faltan or nom in mal_tam:
            estado, det = 'FALTA', 'no esta en la carpeta o cambio de tamano'
        elif not v or v['resultado'] == 'SIN_CONTROL':
            estado, det = 'SIN_VERIFICAR', 'sin revision' if not v else 'tipo sin control'
        elif v['resultado'] == 'MAL':
            estado, det = 'DANADA', 'su lugar lo ocupa el sistema nuevo' if pis else v['detalle']
        elif v['resultado'] == 'REVISAR':
            estado, det = 'REVISAR', v['detalle']
        elif pis:
            estado, det = 'REVISAR', 'se lee, pero su lugar figura ocupado por el sistema nuevo'
        if c['comparte_con']:
            det = (det + ' | ' if det else '') + 'comparte espacio en el indice con: ' + c['comparte_con']
            estado = 'REVISAR' if estado == 'COMPLETA' else estado
        filas.append([nom, estado, c['bytes'], c['modificado_utc'], v['resultado'] if v else '', det, c['sha256']])
    cnt = collections.Counter(f[1] for f in filas)
    say('estados: %s' % dict(cnt))
    with open(os.path.join(final, 'REPORTE.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['archivo', 'estado', 'bytes', 'modificado_utc', 'control', 'detalle', 'sha256'])
        w.writerows(filas)
    with open(os.path.join(final, 'SHA256.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.writelines('%s  %s\n' % (x[6], x[0]) for x in filas)
    total = sum(int(x[2]) for x in filas)
    tipos = collections.Counter(os.path.splitext(x[0])[1].lower() or '(sin)' for x in filas)
    L = ['RECUPERACION DE UN DISCO FORMATEADO', '=' * 35, '',
         'Archivos: %d (%.1f GiB)' % (len(filas), total / 2 ** 30),
         'Por tipo: ' + ', '.join('%s %d' % kv for kv in tipos.most_common(12))]
    if pendientes:
        L.append('OJO: quedan %d archivos del indice sin copiar.' % pendientes)
    L += ['', 'ESTADOS (columna "estado" de REPORTE.csv, se abre con Excel)'] + ['  %s: %d' % kv for kv in cnt.most_common()]
    L += ['', 'CARPETA_SIN_NOMBRE_n: el formateo piso el nombre de esa carpeta; lo de adentro conserva el suyo.',
          'PAPELERA: lo que estaba en la papelera del disco.', '', 'QUE SE COMPRUEBA CON "verificar"',
          '  JPG: se decodifica entero. RAW (ARW/NEF/CR2/DNG): estructura, sin bloques de 64 KB en blanco y la imagen que',
          '  lleva adentro abre; el RAW en si NO se revela. Videos MP4/MOV: estructura (no se reproducen).', '',
          'Las huellas de SHA256.txt se calculan al copiar. El disco original se lee en solo lectura. Si no se hizo una',
          'imagen completa, no formatearlo ni reusarlo hasta revisar lo recuperado.']
    with open(os.path.join(final, 'LEEME.txt'), 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description='Recuperar un disco NTFS formateado con el indice anterior (solo lectura).')
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('diagnostico')
    s.add_argument('origen')
    s.add_argument('--muestras', type=int, default=6000)
    s.add_argument('--log')
    s = sp.add_parser('inventario')
    s.add_argument('copia')
    s.add_argument('run')
    s = sp.add_parser('copiar')
    s.add_argument('origen')
    s.add_argument('run')
    s.add_argument('--tanda', required=True)
    s.add_argument('--sin', action='append', default=[], help='carpeta a dejar afuera (se puede repetir)')
    s.add_argument('--solo', action='append', default=[], help='copiar solo esta carpeta (se puede repetir)')
    s.add_argument('--simular', action='store_true')
    s.add_argument('--reserva-gib', type=float, default=15, help='parar si quedan menos GiB libres')
    s.add_argument('--tope-origen', type=int, default=0, help='prueba: solo archivos que terminan antes de este byte')
    s = sp.add_parser('verificar')
    s.add_argument('run')
    s.add_argument('tanda')
    s = sp.add_parser('entrega')
    s.add_argument('run')
    a = ap.parse_args()
    if getattr(a, 'log', None):
        abrir_log(a.log)
    code = 0
    try:
        {'diagnostico': cmd_diagnostico, 'inventario': cmd_inventario, 'copiar': cmd_copiar,
         'verificar': cmd_verificar, 'entrega': cmd_entrega}[a.cmd](a)
    except SystemExit as ex:
        say('ERROR: %s' % ex)
        code = 1
    except Exception:
        say(traceback.format_exc())
        code = 2
    say('== FIN (codigo %d)' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
