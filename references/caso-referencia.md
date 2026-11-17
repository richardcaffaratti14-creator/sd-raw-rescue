# Caso de referencia: tarjeta de 32 GB de una Sony A7R V

Resumen de la recuperación que originó esta skill, para ver cómo se encadenan las herramientas y qué números esperar. Los datos del evento y de los clientes se omiten a propósito.

## La tarjeta

SD de 32 GB, FAT32, clusters de 32 KB, 979 392 clusters de datos, área de datos desde el byte 8 388 608. Imagen de 32,1 GB. Contenido: una sesión de 58 fotos ARW de ~64 MB (A7R V), 8 NEF de ~26-34 MB de una Nikon D7200 de 2021 que habían quedado al final, unos 26 GB de video AVCHD y restos de fotos más viejas.

Síntomas que trajo el usuario: Windows mostraba un archivo con nombre ilegible en `DCIM\100MSDCF`; PhotoRec devolvió `.sr2` que en miniatura se veían bien y al revelar tenían bandas magenta; TestDisk listaba un directorio con basura.

## Cómo se resolvió (en orden)

| Paso | Herramienta | Resultado |
|---|---|---|
| Imagen | `rescatar_sd.py image` (como administrador) | 12,7 minutos a 42 MB/s, SHA-256, 0 sectores ilegibles |
| Diagnóstico | `rescatar_sd.py scan` | FAT1 ≠ FAT2 en 33 536 de 979 394 entradas; solo 47 cadenas con firma de foto; `DCIM/100MSDCF` clasificada `OTRO` |
| Mapa de la zona dañada (clusters 921 000–979 393) | `mapa_clusters.py` | 31 cabeceras TIFF, 171 saltos RAW\|RAW con costo > 120, 84 fuertes; **período 383 clusters** (71 % de los intervalos) |
| Volcados | `buscar_metadatos.py` | **3 volcados** de metadatos dentro de los datos (clusters 939 554–939 566, 945 684–945 693, 956 625–956 628) con directorio (14, 16 y 8 entradas por sector) y páginas de FAT |
| Directorio y FAT | `ejemplos_caso/dumpdir.py`, `buildfat.py` | 46 entradas de foto con cluster inicial, tamaño y fecha originales; FAT fusionada |
| Modelo de escritura | `ejemplos_caso/unswap.py`, `pieces.py`, `flujo.py` | 124 unidades de 383 clusters con **pares adyacentes intercambiados** (ancla en el cluster 924 673); cada volcado acorta la unidad que lo contiene (376, 379 y 377 clusters); la prueba del largo coincidió en 19 de las 20 cabeceras comprobables (la otra es la última foto, cortada) |
| Fotos en vuelo | `sintesis.fill_rows` | cada volcado le comió datos a la foto que se estaba escribiendo: huecos de 47 y 57 clusters en dos fotos, uno de 674 clusters (36 % de la imagen) en otra, pérdidas de punta de 11 % y 9,5 % en dos más |
| Borradas | `carve_cabeceras.py`, `ejemplos_caso/borradas.py` | 3 fotos borradas enteras; una cuyo principio (401 clusters = 12 % de arriba) nunca se grabó → `PARCIAL` |
| Última foto | `ejemplos_caso/reparar4.py` | la cámara se apagó: solo 1604 de 1938 clusters; 18 % rehecho |
| NEF viejos | `carve_cabeceras.py` / `ejemplos_caso/nef.py` | 7 abren; el 8.º quedó al 72 % (el final de la tarjeta) y solo sirve su JPEG |
| Empalmes | `arreglar_empalmes.py` | filas sueltas dañadas al comienzo de unidades, corregidas con `r−2`/`r+2` |
| Cabeceras pisadas | `rehacer_cabecera.py` | 2 fotos con cabecera prestada de la vecina de la ráfaga |
| Verificación y entrega | LibRaw, hojas de contacto, `ejemplos_caso/armar_final.py` | carpeta con RAW, JPG, reporte, LEEME, SHA-256 y herramienta |

## Resultado

58 ARW: **44 COMPLETA**, **13 REPARADA** (36 %, 18 %, 11 %, 9,5 % de filas rehechas en cuatro; menos del 3 % en las otras nueve) y **1 PARCIAL**. 12 nombres deducidos por numeración. 5 JPG generados desde el RAW (los de cámara estaban rotos). 8 NEF (7 sanos). Todos decodifican con LibRaw; revisados en hojas de contacto. **No probado en Lightroom ni Photoshop.** Duración total: unas dos horas y media de trabajo con iteraciones.

## Lecciones

- El diagnóstico clave fue **buscar dentro de los datos las copias del directorio** (regex sobre la imagen entera, 2-3 minutos): sin ellas los nombres y tamaños originales se perdían.
- El período de los saltos (383) y la prueba del largo permitieron **demostrar** el modelo de pares intercambiados en vez de adivinarlo.
- Pulir antes de validar el modelo hace perder tiempo: primero armar en bruto y medir el largo; después reparar.
- Las heurísticas automáticas de QA (RAW contra JPEG, desplazamiento de filas) dieron falsos positivos; lo que decidió fue mirar hojas de contacto y comparar lado a lado.
- Un error de claves en el diccionario de estados marcó todo como `COMPLETA`: siempre contar estados contra lo esperado.
- La tarjeta tuvo una falla de escritura (de la tarjeta o de la cámara: no se puede saber cuál); se recomendó no usarla más para trabajo.
