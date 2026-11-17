import numpy as np

SP = r'TRABAJO'
z = np.load(SP + r'\full.npz')
types, c0 = z['types'], int(z['c0'])
FATM = np.load(SP + r'\fat_merged.npy')
N = len(types)
print('clusters mapeados: %d  desde %d' % (N, c0))
names = ['ZERO', 'RAW', 'JPEG', 'OTRO', 'TIFF']
print('por tipo:', {names[i]: int((types == i).sum()) for i in range(5)})

# clusters que ya tienen dueno: cadenas de las 46 entradas del directorio (plan) + intervalos conocidos
dirs = [(129999, 64307200), (833221, 64299008), (859130, 64299008), (861093, 64303104), (870978, 66482176), (873007, 66691072),
        (875043, 67072000), (877090, 67104768), (879138, 67129344), (881187, 67350528), (883243, 67317760), (885298, 66310144),
        (887322, 66252800), (889344, 67383296), (891401, 67665920), (893466, 67821568), (895536, 67272704), (897589, 65400832),
        (899585, 65482752), (901584, 66719744), (903621, 66703360), (905657, 66756608), (907695, 66670592), (909730, 65622016),
        (911733, 65601536), (913735, 65589248), (915737, 66605056), (917770, 66670592), (919805, 66682880), (921840, 63725568)]
CS = 32768
owned = np.zeros(N + 2, dtype=bool)
for c, sz in dirs:
    x, k = c, 0
    while 2 <= x < len(FATM) and k < 100000:
        owned[x - c0] = True
        v = int(FATM[x])
        if v >= 0x0FFFFFF8 or v == 0 or v >= len(FATM):
            break
        x = v
        k += 1
# region danada 923785..972147 se trata aparte
isr = (types == 1)
isr[:0] = False
orph = isr & ~owned[:N]
# excluir la region danada (se analiza con la tabla de colocacion)
lo, hi = 923785 - c0, 972148 - c0
orph_out = orph.copy()
orph_out[lo:hi] = False
runs = []
i = 0
while i < N:
    if orph_out[i]:
        j = i
        while j + 1 < N and orph_out[j + 1]:
            j += 1
        if j - i + 1 >= 8:
            runs.append((c0 + i, c0 + j, j - i + 1))
        i = j + 1
    else:
        i += 1
print('trozos RAW huerfanos (>=8 clusters) fuera de la region danada: %d' % len(runs))
tot = sum(r[2] for r in runs)
print('total clusters huerfanos: %d (%.1f MB)' % (tot, tot * CS / 1e6))
for a, b, n in runs[:80]:
    print('   %d..%d  (%d)' % (a, b, n))
tif = [(c0 + int(i)) for i in np.where(types == 4)[0]]
print('cabeceras TIFF en toda la tarjeta: %d' % len(tif))
print(tif[:100])
