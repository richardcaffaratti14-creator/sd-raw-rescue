---
name: sd-raw-rescue
description: Rescata fotos RAW (Sony ARW/SR2, Nikon NEF, Canon CR2, DNG) y JPG de tarjetas SD/CF dañadas, borradas, formateadas o con el directorio roto, siempre en SOLO LECTURA y sobre una imagen 1:1 de la tarjeta. Rearma lo que PhotoRec/TestDisk dejan roto (bandas magenta, RAW que se ve bien en miniatura y se rompe al abrirlo, nombres con basura en DCIM, archivos de 0 bytes) usando la FAT y los volcados de directorio que quedan en la tarjeta, saca fotos borradas por cabecera TIFF y repara filas faltantes de un ARW con el JPEG embebido. Usar SIEMPRE que el usuario quiera recuperar fotos o archivos de una memoria o tarjeta SD/CF de cámara, mencione PhotoRec, TestDisk, Recuva, "la tarjeta pide formatear", fotos borradas o rotas de un evento, RAW/ARW/NEF/CR2 que no abren, aunque no pida nada forense. SD card photo recovery, corrupted or deleted RAW/JPG, FAT32, exFAT.
compatibility: Windows con Python 3 y numpy, pillow, rawpy. Leer la tarjeta cruda exige consola como Administrador. Hace falta espacio libre igual al tamaño de la tarjeta para la imagen.
---

# Rescate de fotos RAW de tarjetas dañadas

Un fotógrafo con la tarjeta de un evento rota no necesita "un programa": necesita recuperar **cada foto, con su nombre, entera y abriendo en Lightroom**, y saber con certeza cuáles quedaron sanas y cuáles no. PhotoRec y TestDisk fallan justo ahí: cortan archivos suponiendo que están contiguos y los mezclan cuando no lo están. Este flujo sale mejor porque **usa la estructura real de la tarjeta (FAT, directorio y hasta volcados de metadatos sueltos), valida cada archivo contra la estructura del propio RAW y repara solo lo que hace falta, dejándolo marcado**.

Las herramientas están en `scripts/` (la ruta base la muestra Claude Code al cargar la skill). Resultado de referencia, con una tarjeta de 32 GB de una Sony A7R V: 58 ARW + 8 NEF; de los ARW, 44 enteras, 13 reparadas (menos del 3 % de filas rehechas en nueve de ellas) y 1 parcial, con el nombre original salvo 12 deducidos por numeración, mientras PhotoRec dejaba bandas magenta en las de la zona dañada. Detalle en `references/caso-referencia.md`.

## Reglas que no se negocian (y por qué)

1. **Solo lectura sobre la tarjeta, siempre.** Cualquier escritura (chkdsk, formatear, "reparar" de Windows, PhotoRec en la misma tarjeta, copiar fotos nuevas) puede pisar justo lo que se quiere salvar. Si la tarjeta sigue en uso, pedir que se deje de usar.
2. **Imagen primero, después se trabaja sobre la imagen.** Una copia 1:1 se lee una sola vez (una tarjeta dañada puede morir) y permite repetir los análisis cien veces sin riesgo. Hace falta espacio libre igual al tamaño de la tarjeta.
3. **Medir antes de creer.** Cada hipótesis sobre cómo se rompió la tarjeta se confirma con una prueba objetiva (largo de la foto contra lo que dice su cabecera, costo de empalme entre clusters, decodificar con LibRaw). Un patrón que funcionó en una tarjeta no se da por cierto en otra.
4. **Todo lo rehecho se rotula.** Estados: `COMPLETA` (bytes tal cual salieron de la tarjeta), `REPARADA` (se rehicieron filas o cabecera, con el porcentaje), `PARCIAL`. Las filas sintetizadas desde el JPEG tienen calidad JPEG, no de RAW: decirlo.
5. **Trabajo reproducible y con copias.** Scripts que se pueden volver a correr, backup antes de re-sintetizar, logs en archivo. Nada va a git ni se sube a ningún servicio: son fotos de clientes.
6. **No borrar la imagen ni las carpetas de trabajo sin preguntar** (la imagen ocupa 32 GB, pero es la única copia fiel de la tarjeta hasta que el usuario valide las fotos).

## Antes de empezar

Preguntar poco y mirar mucho. Lo útil: letra de la tarjeta, cámara y formato (ARW/NEF/CR2), cuántas fotos se esperan (es el mejor control de que no falta nada), si ya corrieron PhotoRec o TestDisk y dónde quedó su salida (los nombres `fNNNNNNN` de PhotoRec son el **sector** donde empieza cada fragmento: sirven de pista con `--ref`), y si siguieron usando la tarjeta. Chequear Python (`python --version`), el espacio libre y que `numpy`, `pillow` y `rawpy` estén instalados (`pip install numpy pillow rawpy`).

El usuario típico es fotógrafo, no programador: escribe en español rioplatense, a veces todo en mayúsculas, y pide "los pasos". Si da permiso, hacer las cosas uno mismo (ver la receta de elevación más abajo). Si tiene que ejecutar algo, un comando por bloque, para PowerShell, diciendo dónde pegarlo y qué debería ver.

## Flujo

| Fase | Objetivo | Herramienta |
|---|---|---|
| 0. Diagnóstico | ver cómo está la FAT, el directorio y las cadenas | `rescatar_sd.py scan E:` |
| 1. Imagen | copia 1:1 con SHA-256 y conteo de sectores ilegibles | `rescatar_sd.py image E: --out tarjeta.img` |
| 2. Camino rápido | seguir las cadenas de la FAT y validar cada foto | `rescatar_sd.py extract tarjeta.img --out FOTOS` |
| 3. Forense | si hay fotos `CORTO`, `SIN_ESTRUCTURA` o mezcladas | `mapa_clusters.py`, `buscar_metadatos.py` → `references/forense.md` |
| 4. Borradas | fotos sin entrada de directorio ni FAT | `carve_cabeceras.py` |
| 4b. exFAT / JPG | tarjeta exFAT (64 GB o más) o JPG borrados | `exfat_jpg.py`: `info`, `escanear`, `extraer`, `carvar`, `hojas` |
| 5. Reparar | cabecera pisada, hueco, filas dañadas | `rehacer_cabecera.py`, `huerfano.py`, `sintesis.py`, `arreglar_empalmes.py`, `fase_filas.py` |
| 6. Verificar | que cada archivo abra y se vea bien | `ver_raw.py`, `jpgs_camara.py`, `hoja_contacto.py`, `verificar_filas.py` |
| 7. Entregar | carpeta final con reporte honesto | `references/entrega.md` |

**Elegir el camino.** Correr siempre 0, 1 y 2. Si `extract` marca casi todo `OK` (el largo de la cadena coincide con el que declara el TIFF de la foto), saltar a 6 y 7: ya está. Si hay `CORTO` o `LARGO`, o FAT1 y FAT2 difieren en miles de entradas, o `scan` muestra la carpeta de fotos (`DCIM/100MSDCF`) como `OTRO` en vez de `DIR`, ir a 3. Si faltan fotos que el usuario sabe que existían, 4 las busca por cabecera. Si el usuario solo quiere "las miniaturas", `rescatar_sd.py thumbs` saca los JPEG embebidos sin tocar la tarjeta. **Si la tarjeta es exFAT** (`scan` lo avisa; Windows la muestra como "EOS_DIGITAL" en las Canon) **o lo que se busca son JPG borrados o "formateados"**: hacer la imagen y seguir con `exfat_jpg.py info` → `escanear` → `extraer` → `carvar` → `hojas` (casi todo sale por las entradas de directorio borradas y la cadena FAT, que la cámara no limpia).

**Pistas de que hace falta el camino forense** (todas se dieron juntas en la tarjeta de referencia): Windows muestra nombres con basura o la carpeta vacía; PhotoRec devuelve `.sr2` o `.arw` que "se ven bien en la miniatura y se rompen al abrir" o con bandas magenta; FAT1 y FAT2 difieren; `scan` encuentra menos fotos que las esperadas.

## Receta: leer la tarjeta cruda desde Claude Code (Windows)

La consola de Claude Code no es administrador y `\\.\E:` lo exige. Se lanza el script en un proceso elevado (el usuario ve el aviso de Windows y toca "Sí") y su salida va a un `--log`, porque un proceso elevado no se puede capturar:

```powershell
$py  = 'C:\ruta\a\python.exe'            # ruta absoluta; el proceso elevado no hereda el venv
$sk  = 'C:\ruta\a\la\skill\scripts'
$log = 'D:\RESCATE\logs\imagen.txt'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Start-Process -FilePath $py -Verb RunAs -ArgumentList ('"' + $sk + '\rescatar_sd.py" image E: --out "D:\RESCATE\tarjeta.img" --log "' + $log + '"')
```

Avisar al usuario antes de lanzarlo ("te va a aparecer un cartel de Windows, tocá Sí") y después mirar el log hasta ver `== FIN (codigo 0)`; una tarjeta de 32 GB por USB tarda 10-15 minutos. No hace falta elevar para nada más: todo lo que sigue trabaja sobre la imagen. Si el usuario prefiere hacerlo él, `scripts/RESCATAR_TARJETA.bat` hace lo mismo con un menú, se auto-eleva y tiene las mismas cuatro operaciones (diagnóstico, imagen, extraer, miniaturas).

## Trampas conocidas

- **Nombres con basura en Windows no significan fotos perdidas.** Suele ser el directorio pisado; los datos pueden estar enteros.
- **La miniatura sana no prueba nada.** El JPEG embebido está al principio del archivo y sobrevive aunque la tira RAW esté mezclada. Probar siempre con LibRaw (`rawpy`) y mirar la imagen entera.
- **No confiar en el tamaño del directorio ni en el de PhotoRec**: el tamaño correcto sale de la propia cabecera TIFF (`need` en `rescatar_sd.py`, más 1024 bytes de ceros finales en ARW de la A7R V).
- **Las heurísticas de QA dan falsos positivos** (`verificar_filas.py` marca `SHIFT±6/7` en zonas planas de fotos sanas; `ubicar_filas.py` tiene una resolución de decenas de filas). Sirven para saltos grandes y datos ajenos (`AJENO`); lo definitivo es mirar la foto y las hojas de contacto.
- **Chequear los conteos finales contra lo que se sabe.** Un diccionario de notas con las claves mal hizo que las 58 fotos salieran "COMPLETA"; contar estados y comparar con lo esperado.
- **Un escaneo de regex sobre toda la imagen tarda minutos**; hacerlo una vez, guardar el resultado, y usar `--c0/--n` para regiones.
- **LibRaw devuelve el ARW de la A7R V con márgenes** (9564×6376): la cámara recorta a 9504×6336 (`render_jpg.py` ya lo hace).
- **Un NEF cortado por el final de la tarjeta no decodifica** ni rellenando con ceros: entregar solo su JPEG embebido y decirlo.
- **Los nombres deducidos por numeración** (cuando el directorio no tiene la entrada) hay que marcarlos como deducidos.
- **exFAT: al borrar, la cámara deja intactos nombre, fecha, tamaño y cluster inicial, y NO limpia la cadena FAT de los archivos fragmentados.** Un archivo "cortado" puede estar entero siguiendo la FAT (`extraer` lo hace); no darlo por perdido sin probarlo.
- **La cámara reutiliza el espacio de lo borrado.** Una foto vieja que comparte clusters con una entrada más nueva está pisada en esa parte. Comparar los clusters reales de cada entrada (cadena FAT incluida), no suponer contigüidad: si no, se marcan como pisadas las vecinas sanas de un archivo fragmentado. Las pisadas por completo no se recuperan: listarlas en el reporte con qué las pisó.
- **Un directorio "con una sola foto" puede esconder cientos**: Windows deja de leer en el primer marcador de fin de directorio y de ahí en más hay conjuntos borrados o en uso que `escanear` sí encuentra. Si en un hueco de numeración faltan entradas (páginas del directorio pisadas) y la cantidad de JPG sin entrada coincide con los números libres entre dos fotos conocidas, se pueden nombrar por numeración (marcándolos como deducidos). El MakerNote de la Canon 70D no trae número de archivo.
- **PowerShell:** no usar `2>&1` sobre ejecutables nativos; no hacer esperas largas con `Start-Sleep` (correr en segundo plano o consultar el log en llamadas cortas).

## Herramientas (`scripts/`)

| Script | Qué hace |
|---|---|
| `rescatar_sd.py` | Herramienta general en un archivo, solo biblioteca estándar de Python (con el `.bat` alcanza para el camino rápido): `scan`, `image`, `extract`, `thumbs`. Lee volumen o imagen, FAT32, cadenas, veredicto por foto, `--ref` para cruzar con PhotoRec. Importable (`Fat32`, `Tiff`, `jpeg_scan`, `find_jpegs`). Lo demás necesita numpy, pillow y rawpy. |
| `exfat_jpg.py` | exFAT, solo lectura sobre la imagen (numpy y pillow). `info`: geometría, bitmap, árbol vivo. `escanear`: una pasada por toda la imagen (35 s para 63 GB) que junta los conjuntos de entradas de directorio vivos, borrados y huérfanos, validados por su suma de verificación, las cabezas JPEG alineadas a cluster y los clusters en cero. `extraer`: por entrada, con nombre, fecha y tamaño originales; sigue la cadena FAT de los fragmentados y marca los pisados por entradas más nuevas. `carvar`: por cabecera, acotado a la próxima cabeza (lo cortado sale `PARCIAL`). `hojas`: contactos. |
| `mapa_clusters.py` | Tipo de cada cluster (ZERO/RAW/JPEG/TIFF/OTRO), cabeceras con tamaño esperado, saltos de empalme y su **período** (tamaño de la unidad de escritura). |
| `buscar_metadatos.py` | Entradas de directorio y páginas de FAT sueltas dentro del área de datos (volcados). |
| `carve_cabeceras.py` | Cabeceras TIFF alineadas a cluster; estado en la FAT; extrae las borradas asumiendo archivo contiguo; `--verificar` con LibRaw. |
| `sintesis.py` | Códec ARW2 (decodifica/codifica bloques) y `fill_rows`: rehace filas desde el JPEG embebido con tres modelos y elige el de menor error de validación. |
| `fase_filas.py` | Dónde empieza cada fila en una tira sin cabecera (precisión de 32 bytes). |
| `arreglar_empalmes.py` | Detecta y corrige filas sueltas dañadas en empalmes (promedio de r−2 y r+2). |
| `rehacer_cabecera.py` | Cabecera pisada: copia la de una foto vecina de la ráfaga y corrige fechas y posición de la tira. |
| `huerfano.py` | Foto sin cabecera ni principio: cola de tira + cabecera donante + filas negras arriba. |
| `verificar_filas.py`, `ubicar_filas.py` | Alinean filas del RAW contra el JPEG embebido (detectan trozos fuera de lugar o ajenos). |
| `ver_raw.py`, `jpgs_camara.py`, `hoja_contacto.py`, `render_jpg.py` | Revisión visual: vistas LibRaw, JPEG de cámara, hojas de contacto, JPG revelado desde RAW. |
| `ejemplos_caso/` | Scripts reales del caso de referencia (ver su `README.md`): cómo se combinaron las piezas. Con rutas y números de aquella tarjeta; sirven de plantilla, no se corren tal cual. |

**Alcance de formatos de foto.** Probado a fondo con Sony ARW (A7R V) y Nikon NEF (D7200). CR2 y DNG comparten el contenedor TIFF y deberían funcionar con las herramientas generales (`rescatar_sd.py`, `carve_cabeceras.py`), sin probar; CR3, RAF y otros formatos con firma distinta no están cubiertos (sí el método de `references/forense.md`). JPG: `exfat_jpg.py`, probado con 1389 JPG de una Canon EOS 70D (todos decodifican y coinciden uno a uno con las cabeceras de la tarjeta). CR2 de esa misma tarjeta: 17 extraídos por entrada de directorio y su JPEG embebido con `rescatar_sd.save_jpegs`; no se revelaron como RAW.

**Alcance de formatos de tarjeta.** FAT32 (hasta 32 GB, lo habitual en cámaras): todo. exFAT (64 GB o más): `image` funciona (probado con una tarjeta real de 64 GB; no depende del sistema de archivos), `exfat_jpg.py` recupera JPG (entradas de directorio, cadena FAT y cabeceras; probado con esa tarjeta) y `carve_cabeceras.py` y `mapa_clusters.py` leen la geometría del boot sector (probado solo con una imagen exFAT sintética). `scan` y `extract` son de FAT32 y avisan con un error claro. En Linux o macOS se puede pasar el dispositivo (`/dev/sdX`, con `sudo`) como origen; no probado.

Constantes de la **A7R V** (fila de 9600 bytes, 6376 filas, recorte 32,20) están al principio de `sintesis.py`, `huerfano.py` y `fase_filas.py`; para otro modelo ARW2 leerlas del SubIFD y ajustarlas (`references/arw2-sony.md`). Para NEF/CR2 sirven `rescatar_sd.py`, `carve_cabeceras.py` y la parte forense; la síntesis de filas solo existe para ARW2.

## Qué referencia leer cuando

- `references/forense.md`: cuando el camino rápido no alcanza. Síntomas, volcados de metadatos, reconstrucción de directorio y FAT, unidades de escritura con pares intercambiados, prueba del largo, fotos en vuelo, huérfanas.
- `references/arw2-sony.md`: antes de tocar una tira ARW (formato, códec, fase de filas, modelos de síntesis, adaptar a otro modelo, notas de NEF).
- `references/entrega.md`: al armar la carpeta final, el reporte y el mensaje al usuario.
- `references/caso-referencia.md`: para ver cómo se aplicó todo en una tarjeta real, con cifras.

## Cierre con el usuario

Decir en pocas líneas: dónde quedó todo; cuántas fotos hay por estado (completas / reparadas / parciales) y cuáles; qué porcentaje se rehízo en las reparadas y con qué calidad; qué nombres son deducidos; qué **no** se verificó (por ejemplo, abrir en Lightroom o Photoshop si solo se probó LibRaw); que la tarjeta no se escribió; y que la imagen de la tarjeta se puede borrar cuando valide, pidiéndole permiso antes. Ofrecer, sin hacerlo, lo que quedó fuera (video AVCHD, fotos viejas sueltas).
