"""Relleno de filas faltantes de un ARW (Sony A7R V, ARW2 8 bpp por pixel) a partir del JPEG embebido de la misma foto.
 1) decodifica filas buenas del RAW (bloques de 16 bytes) y las compara con el JPEG -> regresion lineal por canal CFA
 2) sintetiza las filas faltantes desde el JPEG y las codifica de vuelta en bloques ARW2 validos
Uso como libreria:  fill_rows(raw_bytearray, so, jpeg_bytes, r0, r1, fit_rows=None)
CONSTANTES de la A7R V (ILCE-7RM5): fila de W=9600 bytes, 6376 filas, recorte (32,20), JPEG full 9504x6336.
Para otro modelo ARW2 ajustar W, CROP_X, CROP_Y y los 6376 / 6330 de fill_rows (ver references/arw2-sony.md).
"""
import io

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
W = 9600
NB = W // 16          # 600 bloques por fila
CROP_X, CROP_Y = 32, 20


def make_curve(knees=(8000, 10400, 12900, 14100)):
    sc = [0] + [(k >> 2) & 0xFFF for k in knees] + [4095]
    curve = np.arange(0x10000, dtype=np.int64)
    curve[0] = 0
    for i in range(5):
        for j in range(sc[i] + 1, sc[i + 1] + 1):
            curve[j] = curve[j - 1] + (1 << i)
    return curve


CURVE = make_curve()
L_OF_P = (CURVE[np.arange(2048) * 2] >> 2).astype(np.float64)           # lineal por codigo 0..2047


def p_of_L(L):
    """codigo 11 bit cuyo lineal es el mas cercano a L (L_OF_P es monotona creciente)"""
    idx = np.searchsorted(L_OF_P, L)
    idx = np.clip(idx, 1, 2047)
    lo, hi = L_OF_P[idx - 1], L_OF_P[idx]
    return np.where((L - lo) < (hi - L), idx - 1, idx).astype(np.int32)


def decode_rows(buf, so, rows):
    """buf: bytes-like del archivo. Devuelve p (len(rows), 9600) int32 con los codigos de pixel (orden de columnas del sensor)."""
    out = np.zeros((len(rows), W), dtype=np.int32)
    for n, r in enumerate(rows):
        blk = np.frombuffer(buf, dtype=np.uint8, count=W, offset=so + r * W).reshape(NB, 16)
        w0 = blk[:, :4].copy().view('<u4').ravel().astype(np.int64)
        lo = blk[:, :8].copy().view('<u8').ravel()
        hi = blk[:, 8:].copy().view('<u8').ravel()
        mx = (w0 & 0x7FF).astype(np.int64)
        mn = ((w0 >> 11) & 0x7FF).astype(np.int64)
        imax = ((w0 >> 22) & 0xF).astype(np.int64)
        imin = ((w0 >> 26) & 0xF).astype(np.int64)
        diff = mx - mn
        sh = np.zeros(NB, dtype=np.int64)
        for s in range(4):
            sh += (((0x80 << s) <= diff) & (sh == s)).astype(np.int64)
        pix = np.zeros((NB, 16), dtype=np.int64)
        for i in range(16):
            j = i - (imax < i).astype(np.int64) - (imin < i).astype(np.int64)
            b = 30 + 7 * j                       # bit inicial del delta
            b = np.clip(b, 0, 121)
            bu = b.astype(np.uint64)
            f_lo = (lo >> bu) & np.uint64(0x7F)
            f_hi = (hi >> (bu - np.uint64(64)).astype(np.uint64)) & np.uint64(0x7F)
            f_mid = ((lo >> bu) | (hi << (np.uint64(64) - bu))) & np.uint64(0x7F)
            d = np.where(b + 7 <= 64, f_lo, np.where(b >= 64, f_hi, f_mid)).astype(np.int64)
            v = np.minimum((d << sh) + mn, 0x7FF)
            v = np.where(imax == i, mx, np.where(imin == i, mn, v))
            pix[:, i] = v
        # columnas: bloque b -> tramo s=b//2, paridad q=b%2 ; pixel i -> col = 32 s + q + 2 i
        row = np.zeros(W, dtype=np.int32)
        bs = np.arange(NB)
        for i in range(16):
            cols = 32 * (bs // 2) + (bs % 2) + 2 * i
            row[cols] = pix[:, i]
        out[n] = row
    return out


def encode_rows(P):
    """P: (n,9600) int32 codigos 0..2047 -> bytes (n*9600) de bloques ARW2 validos"""
    n = P.shape[0]
    out = np.zeros((n, NB, 16), dtype=np.uint8)
    bs = np.arange(NB)
    for r in range(n):
        pix = np.zeros((NB, 16), dtype=np.int64)
        for i in range(16):
            cols = 32 * (bs // 2) + (bs % 2) + 2 * i
            pix[:, i] = P[r, cols]
        mx = pix.max(axis=1)
        mn = pix.min(axis=1)
        imax = pix.argmax(axis=1)
        pm = pix.copy()
        pm[np.arange(NB), imax] = 4096
        imin = pm.argmin(axis=1)
        same = imax == imin
        imin = np.where(same, (imax + 1) % 16, imin)          # evita imax==imin en bloques planos
        diff = mx - mn
        sh = np.zeros(NB, dtype=np.int64)
        for s in range(4):
            sh += (((0x80 << s) <= diff) & (sh == s)).astype(np.int64)
        lo = (mx | (mn << 11) | (imax << 22) | (imin << 26)).astype(np.uint64)
        hi = np.zeros(NB, dtype=np.uint64)
        for i in range(16):
            j = i - (imax < i).astype(np.int64) - (imin < i).astype(np.int64)
            use = (imax != i) & (imin != i)
            d = np.clip((pix[:, i] - mn) >> sh, 0, 127).astype(np.uint64)
            b = 30 + 7 * j
            bu = np.clip(b, 0, 121).astype(np.uint64)
            lo_part = np.where(use & (b < 64), d << bu, np.uint64(0))
            lo = lo | lo_part.astype(np.uint64)
            cross = use & (b < 64) & (b + 7 > 64)
            hi_cross = np.where(cross, d >> (np.uint64(64) - bu), np.uint64(0))
            hi_hi = np.where(use & (b >= 64), d << (bu - np.uint64(64)), np.uint64(0))
            hi = hi | hi_cross.astype(np.uint64) | hi_hi.astype(np.uint64)
        out[r, :, :8] = lo.view(np.uint8).reshape(NB, 8)
        out[r, :, 8:] = hi.view(np.uint8).reshape(NB, 8)
    return out.reshape(-1).tobytes()


def srgb_to_lin(x):
    x = x.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def cfa_channel(r, cols):
    """RGGB: fila par -> R en col par, G en col impar ; fila impar -> G en col par, B en col impar"""
    even_r = (r % 2 == 0)
    even_c = (cols % 2 == 0)
    ch = np.where(even_r, np.where(even_c, 0, 1), np.where(even_c, 1, 2))
    return ch


class Jpg:
    def __init__(self, jpeg_bytes):
        im = Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')
        self.arr = np.asarray(im)            # (6336, 9504, 3)
        self.h, self.w = self.arr.shape[:2]

    def row_lin(self, r):
        y = min(max(r - CROP_Y, 0), self.h - 1)
        row = self.arr[y]                                   # (9504,3)
        xs = np.clip(np.arange(W) - CROP_X, 0, self.w - 1)
        return srgb_to_lin(row[xs])                         # (9600,3)


def fit(buf, so, jpg, fit_rows):
    P = decode_rows(buf, so, fit_rows)
    X = {0: [], 1: [], 2: []}
    Y = {0: [], 1: [], 2: []}
    cols = np.arange(W)
    for n, r in enumerate(fit_rows):
        lin = jpg.row_lin(r)
        ch = cfa_channel(r, cols)
        L = L_OF_P[P[n]]
        for c in range(3):
            m = ch == c
            X[c].append(lin[m])
            Y[c].append(L[m])
    beta = {}
    rep = []
    for c in range(3):
        x = np.vstack(X[c])
        y = np.concatenate(Y[c])
        A = np.hstack([x, np.ones((len(x), 1), dtype=np.float32)])
        b, *_ = np.linalg.lstsq(A, y, rcond=None)
        pred = A @ b
        ss = float(((y - pred) ** 2).sum())
        st = float(((y - y.mean()) ** 2).sum())
        rep.append(1 - ss / max(st, 1e-9))
        beta[c] = b
    return beta, rep


def synth_rows(jpg, beta, rows):
    cols = np.arange(W)
    P = np.zeros((len(rows), W), dtype=np.int32)
    for n, r in enumerate(rows):
        lin = jpg.row_lin(r)
        ch = cfa_channel(r, cols)
        L = np.zeros(W, dtype=np.float64)
        for c in range(3):
            m = ch == c
            A = np.hstack([lin[m], np.ones((m.sum(), 1), dtype=np.float32)])
            L[m] = A @ beta[c]
        P[n] = p_of_L(np.maximum(L, 0))
    return P


def fit_lut(buf, so, jpg, fit_rows):
    """Tabla por canal CFA: valor 8 bit del JPEG (mismo canal) -> lineal medio del RAW (monotona)."""
    P = decode_rows(buf, so, fit_rows)
    cols = np.arange(W)
    acc = {c: [np.zeros(256), np.zeros(256)] for c in range(3)}
    for n, r in enumerate(fit_rows):
        y = min(max(r - CROP_Y, 0), jpg.h - 1)
        xs = np.clip(cols - CROP_X, 0, jpg.w - 1)
        rowj = jpg.arr[y][xs]                         # (9600,3) uint8
        ch = cfa_channel(r, cols)
        L = L_OF_P[P[n]]
        for c in range(3):
            m = ch == c
            v = rowj[m, c].astype(np.int64)
            acc[c][0] += np.bincount(v, weights=L[m], minlength=256)
            acc[c][1] += np.bincount(v, minlength=256)
    lut = {}
    for c in range(3):
        s, n = acc[c]
        x = np.arange(256)
        ok = n > 20
        if ok.sum() < 8:
            lut[c] = np.linspace(0, float(L_OF_P[-1]), 256)
            continue
        y = np.interp(x, x[ok], (s[ok] / n[ok]))
        lut[c] = np.maximum.accumulate(y)
    return lut


def synth_rows_lut(jpg, lut, rows):
    cols = np.arange(W)
    P = np.zeros((len(rows), W), dtype=np.int32)
    for i, r in enumerate(rows):
        y = min(max(r - CROP_Y, 0), jpg.h - 1)
        xs = np.clip(cols - CROP_X, 0, jpg.w - 1)
        rowj = jpg.arr[y][xs]
        ch = cfa_channel(r, cols)
        L = np.zeros(W, dtype=np.float64)
        for c in range(3):
            m = ch == c
            L[m] = lut[c][rowj[m, c]]
        P[i] = p_of_L(L)
    return P


KN = 17                                      # nudos del modelo aditivo (cada 16 niveles de 0..255)
KX = np.linspace(0, 255, KN)


def _basis(v):
    """pesos de las dos funciones 'sombrero' activas: devuelve (indice_izq, peso_izq) ; el derecho es indice+1 con peso 1-peso_izq"""
    x = np.clip(v.astype(np.float64), 0, 255) / (255.0 / (KN - 1))
    i = np.minimum(x.astype(np.int64), KN - 2)
    return i, 1.0 - (x - i)


def _design(rowj_c):
    """rowj_c: (n,3) uint8 -> matriz (n, 3*KN+1) con splines lineales por canal JPEG + sesgo"""
    n = rowj_c.shape[0]
    X = np.zeros((n, 3 * KN + 1), dtype=np.float32)
    ar = np.arange(n)
    for k in range(3):
        i, w = _basis(rowj_c[:, k])
        X[ar, k * KN + i] = w
        X[ar, k * KN + i + 1] = 1.0 - w
    X[:, -1] = 1.0
    return X


def fit_add(buf, so, jpg, fit_rows, lam=2.0):
    """Modelo aditivo: lineal_RAW(canal CFA c) = sesgo + sum_k f_ck(JPEG_k)  con f_ck splines lineales (mezcla de color + curva de tono)."""
    P = decode_rows(buf, so, fit_rows)
    cols = np.arange(W)
    D = 3 * KN + 1
    A = {c: np.zeros((D, D)) for c in range(3)}
    B = {c: np.zeros(D) for c in range(3)}
    for n, r in enumerate(fit_rows):
        y = min(max(r - CROP_Y, 0), jpg.h - 1)
        xs = np.clip(cols - CROP_X, 0, jpg.w - 1)
        rowj = jpg.arr[y][xs]
        ch = cfa_channel(r, cols)
        L = L_OF_P[P[n]]
        for c in range(3):
            m = ch == c
            X = _design(rowj[m])
            A[c] += (X.T @ X).astype(np.float64)
            B[c] += X.T.astype(np.float64) @ L[m]
    # penalizacion de segunda diferencia (suaviza y llena nudos sin datos)
    Dm = np.zeros((KN - 2, KN))
    for i in range(KN - 2):
        Dm[i, i:i + 3] = (1, -2, 1)
    Pn = np.zeros((D, D))
    for k in range(3):
        Pn[k * KN:(k + 1) * KN, k * KN:(k + 1) * KN] = Dm.T @ Dm
    coef = {}
    for c in range(3):
        n_c = max(A[c][-1, -1], 1.0)
        reg = lam * (n_c / 1e5) * Pn + 1e-6 * n_c * np.eye(D)
        coef[c] = np.linalg.solve(A[c] + reg, B[c])
    return coef


def synth_rows_add(jpg, coef, rows):
    cols = np.arange(W)
    P = np.zeros((len(rows), W), dtype=np.int32)
    for i, r in enumerate(rows):
        y = min(max(r - CROP_Y, 0), jpg.h - 1)
        xs = np.clip(cols - CROP_X, 0, jpg.w - 1)
        rowj = jpg.arr[y][xs]
        ch = cfa_channel(r, cols)
        L = np.zeros(W, dtype=np.float64)
        for c in range(3):
            m = ch == c
            L[m] = _design(rowj[m]).astype(np.float64) @ coef[c]
        P[i] = p_of_L(np.clip(L, 0, float(L_OF_P[-1])))
    return P


def fill_rows(buf, so, jpeg_bytes, r0, r1, fit_rows=None, jpg=None, method='auto'):
    """Reescribe las filas [r0,r1) de buf (bytearray) con filas sintetizadas desde el JPEG.
    method: 'lin' (regresion RGB), 'lut' (tabla por canal), 'add' (splines aditivos por canal) o 'auto' (el de menor error)."""
    jpg = jpg or Jpg(jpeg_bytes)
    if fit_rows is None:
        local = list(range(max(41, r0 - 900), r0 - 20, 7)) + list(range(r1 + 21, min(6330, r1 + 900), 7))
        glob = [r for r in range(41, 6330, 29 if (r1 - r0) > 640 else 61) if r < r0 - 20 or r > r1 + 20]
        if (r1 - r0) > 640:                   # hueco grande: se ajusta con filas de toda la imagen para ver todos los colores
            cand = glob
        elif (r1 - r0) > 100:                 # hueco mediano: filas cercanas + algunas de toda la imagen
            cand = sorted(set(local) | set(glob))
        else:
            cand = local
        fit_rows = [r for r in cand if 0 <= r < 6376]
    # validacion: filas buenas cerca del hueco que no se usan para ajustar
    val = [r for r in fit_rows[1::5]][:40]
    train = [r for r in fit_rows if r not in set(val)]
    beta, rep = fit(buf, so, jpg, train)
    lut = fit_lut(buf, so, jpg, train)
    chosen = method
    info = dict(metodo=method, err_lin=None, err_lut=None, err_add=None, filas_ajuste=len(fit_rows))
    if method == 'auto':
        chosen = 'lin'
        if val:
            coef = fit_add(buf, so, jpg, train)
            Pv = decode_rows(buf, so, val)
            Lv = L_OF_P[Pv]
            e = {'lin': float(np.abs(L_OF_P[synth_rows(jpg, beta, val)] - Lv).mean()),
                 'lut': float(np.abs(L_OF_P[synth_rows_lut(jpg, lut, val)] - Lv).mean()),
                 'add': float(np.abs(L_OF_P[synth_rows_add(jpg, coef, val)] - Lv).mean())}
            chosen = min(e, key=e.get)
            info.update(err_lin=round(e['lin'], 1), err_lut=round(e['lut'], 1), err_add=round(e['add'], 1))
    info['metodo'] = chosen
    if chosen == 'lut':
        lut = fit_lut(buf, so, jpg, fit_rows)
        P = synth_rows_lut(jpg, lut, list(range(r0, r1)))
    elif chosen == 'add':
        coef = fit_add(buf, so, jpg, fit_rows)
        P = synth_rows_add(jpg, coef, list(range(r0, r1)))
    else:
        beta, rep = fit(buf, so, jpg, fit_rows)
        P = synth_rows(jpg, beta, list(range(r0, r1)))
    buf[so + r0 * W: so + r1 * W] = encode_rows(P)
    fill_rows.info = info
    return [float(x) for x in rep]       # R2 por canal de la regresion lineal (siempre floats)
