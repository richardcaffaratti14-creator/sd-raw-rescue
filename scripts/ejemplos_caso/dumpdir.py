import struct
import sys

IMG = r'D:\RESCATE_SD\sd_E.img'
DATA = 8388608
CS = 32768
f = open(IMG, 'rb')


def sector(c, s):
    f.seek(DATA + (c - 2) * CS + s * 512)
    return f.read(512)


def parse(e):
    name = e[0:8].decode('latin-1')
    ext = e[8:11].decode('latin-1')
    attr = e[11]
    ct = struct.unpack_from('<H', e, 14)[0]
    cd = struct.unpack_from('<H', e, 16)[0]
    wt = struct.unpack_from('<H', e, 22)[0]
    wd = struct.unpack_from('<H', e, 24)[0]
    cl = (struct.unpack_from('<H', e, 20)[0] << 16) | struct.unpack_from('<H', e, 26)[0]
    sz = struct.unpack_from('<I', e, 28)[0]

    def dt(d, t):
        return '%04d-%02d-%02d %02d:%02d:%02d' % (1980 + (d >> 9), (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63, (t & 31) * 2)
    return name, ext, attr, cl, sz, dt(wd, wt)


copies = [(939560, 46, 8), (945688, 24, 8), (956628, 56, 8)]
for c, s0, n in copies:
    print('=== copia en cluster %d, sectores %d..%d' % (c, s0, s0 + n - 1))
    for s in range(s0, s0 + n):
        sec = sector(c, s)
        ents = [sec[i:i + 32] for i in range(0, 512, 32)]
        ok = [e for e in ents if e[0] not in (0, 0xE5) and e[8:11] in (b'ARW', b'JPG', b'HIF', b'   ') and e[11] in (0x20, 0x10, 0x0F)]
        print('  sector %d: %d entradas validas' % (s, len(ok)))
        for e in ok:
            name, ext, attr, cl, sz, w = parse(e)
            print('     %s.%s attr=%02X cluster=%-7d tam=%-9d (%d clusters) mod=%s' % (name, ext, attr, cl, sz, (sz + CS - 1) // CS, w))
