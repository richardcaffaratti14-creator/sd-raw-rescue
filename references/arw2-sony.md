# ARW2 de Sony (A7R V, ILCE-7RM5) y notas de NEF

Leer esto antes de tocar una tira ARW: decodificar, codificar, ubicar filas o rehacer filas faltantes. Todo lo de abajo está verificado con la A7R V (61 MP); para otros modelos ver la sección 7.

Contenido: 1 estructura del archivo · 2 la tira ARW2 · 3 posición de la tira sin cabecera · 4 rehacer filas desde el JPEG · 5 empalmes dañados · 6 cabecera prestada · 7 otro modelo · 8 NEF y otros TIFF.

## 1. Estructura del archivo

TIFF little endian (`II*\0`). IFD0: Make `SONY` (0x10F), Model `ILCE-7RM5` (0x110), fecha (0x132), ExifIFD (0x8769) con `DateTimeOriginal` (0x9003), SubIFDs (0x14A) y la vista previa JPEG 1616×1080 (offset 0x201, largo 0x202, cerca de los 190 KB). Después viene el **JPEG completo de 9504×6336** (unos 2,7 MB, cerca de los 260 KB) y, en el offset `so`, la tira RAW.

En el SubIFD de la imagen RAW: compresión `32767` (ARW2), ancho 9600 (0x100), alto 6376 (0x101), `so` = StripOffsets (0x111), `sc` = StripByteCounts (0x117) = 9600 × 6376 = 61 209 600. `so` cambia de foto a foto (2,2 a 5,9 MB; múltiplo de 4096).

**Tamaño del archivo = so + sc + 1024**: la tira va seguida de 1024 bytes de ceros. Es la forma de saber el largo exacto de una foto sin directorio, y de validarla (`rescatar_sd.py` calcula `need = so + sc`). Si `so` es múltiplo de 32 768, el archivo termina justo en un límite de cluster de 32 KB y la tira acaba a 31 744 bytes del inicio del último cluster.

## 2. La tira ARW2

Un byte por píxel. Una fila = 9600 bytes = 600 bloques de 16 bytes (128 bits):

| bits | contenido |
|---|---|
| 0–10 | `max` (código de 11 bits) |
| 11–21 | `min` |
| 22–25 | `imax`: índice (0–15) del píxel máximo |
| 26–29 | `imin`: índice del píxel mínimo |
| 30–127 | 14 deltas de 7 bits, para los píxeles que no son imax ni imin, en orden |

`sh` = menor `s` tal que `(0x80 << s) > max − min` (tope 4). Píxel = `min((delta << sh) + min, 0x7FF)`; imax e imin valen `max` y `min`.

**Columnas**: cada tramo de 32 columnas son dos bloques. El bloque `b` (0..599) cubre las columnas `32·(b//2) + (b%2) + 2·i`, `i = 0..15`: el par las pares y el impar las impares. Patrón de color **RGGB** (fila par: R,G,R,G…; fila impar: G,B,G,B…).

**Tono**: el código de 11 bits pasa por una curva (tabla de 4 rodillas, la del tag SR2 0x7010) para dar el valor lineal: `L = CURVA[2·p] >> 2`. `sintesis.py` trae `make_curve` con las rodillas `(8000, 10400, 12900, 14100)`.

`sintesis.decode_rows(buf, so, filas)` devuelve los códigos por fila; `sintesis.encode_rows(P)` los vuelve a empaquetar. Comprobado: decodificar → codificar → decodificar da **códigos idénticos** (los bytes pueden diferir en empates de imax/imin, pero decodifican igual). Es el mismo algoritmo que `sony_arw2_load_raw` de LibRaw.

LibRaw entrega la imagen de 9564×6376 con márgenes; la cámara recorta 32 columnas a la izquierda y 20 filas arriba (DefaultCrop) para llegar a 9504×6336, que es la medida del JPEG. La fila RAW `r` se corresponde con la fila JPEG `r − 20`, y la columna `c` con `c − 32`.

## 3. Posición de la tira cuando falta la cabecera

Si se perdió la cabecera, no se conoce `so`, y sin eso no se sabe dónde empieza cada fila.

- **Por el final**: la tira termina donde empiezan los 1024 ceros. Si se sabe en qué cluster y a qué offset acaba, `so = fin − 61 209 600`. Es exacto.
- **Por la fase de filas** (`fase_filas.py IMAGEN CLUSTER N [--so --k0]`): en el sensor RGGB, la diferencia entre el promedio del bloque impar y el par de cada tramo cambia de signo en cada fila; con la fase correcta el signo es coherente a lo largo de los 300 tramos de una fila. Prueba las 300 fases posibles y devuelve cuántos bytes hay desde el inicio del trozo hasta el primer comienzo de fila, con precisión de 32 bytes (con 200 clusters alcanza). Sirve para confirmar un `so` obtenido por el final o por estimación (en la tarjeta de referencia dio exactamente la fase esperada).

Con `so` conocido se puede completar la cabecera (`ejemplos_caso/borradas.py`: pone `so` y `sc` en el SubIFD) o armar un archivo sin principio (`huerfano.py`).

## 4. Rehacer filas faltantes desde el JPEG embebido

El JPEG de 9504×6336 está al principio del archivo y casi siempre sobrevive. Sirve de **referencia visual** para rehacer filas RAW que no existen: `sintesis.fill_rows(buf, so, jpeg_bytes, r0, r1)` escribe en `buf` (un `bytearray`) las filas `[r0, r1)` ya codificadas como bloques ARW2 válidos. Se puede pasar el JPEG de otra copia de la misma foto si el del archivo también se perdió.

Cómo funciona:
1. Toma filas buenas del propio archivo (alrededor del hueco; si el hueco es grande, de toda la imagen para ver todos los colores) y aprende cómo se ve en el RAW cada valor del JPEG.
2. Tres modelos: `lin` (regresión 3×3 + sesgo sobre el JPEG en lineal), `lut` (tabla monótona valor JPEG → lineal RAW, por canal) y `add` (splines lineales aditivos de 17 nudos por canal del JPEG, con penalización de segunda diferencia: capta mezcla de color y curva de tono).
3. Reserva filas de validación (1 de cada 5, hasta 40), entrena los tres sin ellas, **elige el de menor error** y lo reentrena con todas. `fill_rows.info` guarda `metodo`, los tres errores y las filas de ajuste; la función devuelve el R² de la regresión lineal por canal.
4. Filas de ajuste: huecos de hasta 100 filas, cercanas; de 100 a 640, cercanas más globales; mayores a 640, globales cada 29 filas.

Qué pasó en la práctica: el modelo lineal falla en brillos saturados (franja rosa/magenta) → ganó `lut`; en un hueco del 36 % de la imagen el color salía verdoso → con `add` y filas globales quedó fiel. **Siempre mirar la costura** entre filas reales y sintéticas y comparar lado a lado con el JPEG de cámara.

Límites: no recupera detalle ni rango dinámico de RAW (tiene calidad JPEG, 8 bits, con el revelado de la cámara); sin ruido; no sirve si falta el JPEG de referencia. Por eso la foto se rotula `REPARADA` con el porcentaje.

## 5. Empalmes dañados

En la tarjeta de referencia, los primeros ~7 KB de cada unidad de escritura quedaron pisados: una o dos filas sueltas con datos ajenos. Una fila está dañada si difiere mucho de sus vecinas del mismo color (`r−2` y `r+2`) pero esas dos coinciden entre sí (`min(a, b) > 3·max(c, 6)` y `min(a, b) > 25`, medido sobre el lineal). `arreglar_empalmes.py ARCHIVO SO --filas f1,f2,…` las detecta (`--solo-informar` no escribe) y las reemplaza por el promedio de `r−2` y `r+2`, recodificado.

## 6. Cabecera prestada

Cuando la cabecera de una foto quedó pisada (por un volcado de FAT) o nunca se grabó: usar la de una foto vecina de la **misma ráfaga** (`rehacer_cabecera.py FOTO VECINA SO FECHA SALIDA`). Copia IFD0, Exif, MakerNote, miniaturas y SubIFD; corrige las fechas (0x132, 0x9003, 0x9004), `so` y `sc`, y el largo de la vista previa (si la del donante no entra en el espacio, la recodifica con menos calidad). Los datos propios del disparo (exposición, enfoque) quedan los del donante: decirlo. Para una foto sin su vista previa, `ejemplos_caso/borradas.py` genera una desde el propio RAW con LibRaw.

## 7. Otro modelo ARW2

Valores que cambian: `W` (bytes por fila, = ancho del SubIFD), `NB = W/16`, el alto (6376), el recorte `CROP_X, CROP_Y` (DefaultCropOrigin/Size, tags 0xC61F/0xC620, o la diferencia entre el raw y el JPEG), el relleno final (1024) y el `lag` de `mapa_clusters.py` (= 2·W/16). Están como constantes al principio de `sintesis.py`, `huerfano.py`, `fase_filas.py`, `verificar_filas.py` y en `fill_rows`.

No está verificado con otros modelos. Antes de confiar: decodificar una foto sana con `decode_rows`, pasar los códigos por la curva y compararlos con `rawpy.imread(...).raw_image`; y comprobar que `size − (so + sc)` en fotos sanas da el relleno supuesto.

## 8. NEF (Nikon) y otros TIFF

En la tarjeta de referencia, los 8 NEF de una D7200 estaban **contiguos** desde su cabecera, sin entrada en el directorio ni en la FAT: `carve_cabeceras.py` los lista y los extrae con tamaño = `need` del TIFF (sin relleno). El último quedó cortado por el fin de la tarjeta: LibRaw no lo abre ni rellenando con ceros, así que se entrega solo su JPEG embebido (6000×4000; `rescatar_sd.py thumbs`). CR2 y DNG (también TIFF) deberían comportarse igual; CR3, RAF y otros formatos con firma distinta no están cubiertos por los scripts (el método de la parte forense sí vale).
