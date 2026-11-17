# Forense de una tarjeta que no se deja leer por FAT

Se usa cuando `rescatar_sd.py extract` no alcanza: fotos `CORTO`/`LARGO`, mezcladas, o directorio y FAT que no cuentan lo mismo. Todo se hace sobre la **imagen**. El método vale para cualquier tarjeta; los números (383 clusters, pares intercambiados, tres volcados) son los de la tarjeta de referencia y **hay que volver a medirlos** en cada caso.

Contenido: 1 síntomas · 2 geometría y mapa de clusters · 3 volcados de metadatos · 4 reconstruir directorio y FAT · 5 cómo se escribieron los datos · 6 armar los archivos · 7 pérdidas en vuelo y huecos · 8 borradas, huérfanas, fragmentos viejos · 9 verificación en capas.

## 1. Síntomas → hipótesis

| Se ve | Probable causa | Qué mirar |
|---|---|---|
| Nombres con símbolos raros en `DCIM\100MSDCF`, o la carpeta "vacía" | clusters del directorio pisados por datos | `scan`: la carpeta sale `OTRO`; buscar copias con `buscar_metadatos.py` |
| PhotoRec entrega `.sr2`/`.arw` que en miniatura se ven bien y al revelar tienen bandas magenta o filas corridas | el archivo no era contiguo; PhotoRec pegó trozos de otras fotos | el JPEG embebido está al principio y no prueba nada; decodificar la tira |
| FAT1 ≠ FAT2 en miles de entradas | FAT dañada o reescrita a medias | `scan`; reconstruir con los volcados (sección 4) |
| `extract` da `CORTO(nKB)` | la cadena de la FAT es más corta que lo que declara la foto | faltan clusters: ¿FAT incompleta o datos nunca grabados? |
| Menos fotos de las que el usuario espera | borradas, o cabeceras pisadas | `carve_cabeceras.py`, sección 8 |
| Un archivo termina antes de tiempo, el último de la sesión | la cámara se apagó o se llenó la tarjeta | tamaño de la tarjeta; foto `PARCIAL` |

## 2. Geometría y mapa de clusters

`rescatar_sd.py scan` imprime lo básico: bytes por cluster (32 KB en una SD de 32 GB), sectores reservados, FATs, dónde empieza el área de datos (cluster 2) y cuántos clusters hay. Con eso, el offset de un cluster `c` es `datos + (c - 2) * cluster`.

`mapa_clusters.py IMAGEN --c0 A --n N` recorre una región y clasifica **cada cluster**: `ZERO`, `RAW` (bloques ARW2 coherentes: en cada bloque de 16 bytes el máximo es ≥ al mínimo), `JPEG`, `TIFF` (empieza una cabecera) u `OTRO`. Además calcula el **costo de empalme** entre el cluster `i` y el `i+1`: diferencia media entre las últimas 2 filas de bloques de uno y las primeras del otro. Dentro de una foto el costo es ruido (5–40); cuando los clusters vecinos no son de la misma foto o están desordenados sube a cientos. Esa es la herramienta que revela dónde se corta el orden de los datos.

Se puede correr sobre toda la tarjeta (segundos a un par de minutos). Al final imprime el **período** de los saltos fuertes (distancia entre saltos consecutivos que es múltiplo de P). Una tarjeta sana no tiene período; en la de referencia salió 383 clusters (12,5 MB), lo que delató que la cámara escribió en unidades de ese tamaño.

## 3. Volcados de metadatos

`buscar_metadatos.py IMAGEN [--c0 --n]` busca **dentro del área de datos** dos cosas:

- entradas de directorio 8.3 (`DSC01234ARW` + atributo `0x20`), con cluster inicial, tamaño y fecha reales de cada foto; el primer byte `0xE5` marca una borrada;
- sectores con forma de FAT: ≥100 de 128 valores de 32 bits son punteros válidos y ≥20 son distintos de cero. Un run largo con valores consecutivos (`secuencial max` cerca de 127) es una página de FAT.

En la tarjeta de referencia aparecieron **tres volcados** (de 4 a 13 clusters cada uno) en medio de las fotos, cada uno con unos pocos sectores de directorio (8 a 16 entradas por sector) y entre decenas y cientos de sectores de FAT. Cada volcado tiene lo que la cámara conocía en ese momento: por eso hacen falta todos, y las copias de un mismo sector deben coincidir entre sí (se comprueba).

Lo esperable es ver el directorio legítimo en su lugar (cluster ~130 000 en esa tarjeta); lo que importa son los hallazgos fuera de lugar.

## 4. Reconstruir directorio y FAT

1. **Directorio**: parsear las entradas (nombre, `cluster = (hi<<16)|lo` en los bytes 20–21 y 26–27, tamaño en 28–31, hora y fecha en 22–25) de todos los sectores hallados. Ejemplos: `ejemplos_caso/dumpdir.py`, `entregar_intactas.py` (`dir_entries`).
2. **FAT**: cada sector son 128 entradas consecutivas. Si es una cadena contigua, la entrada `k` vale `k+1`, así que el primer valor del sector dice qué entradas cubre (`((v0-1)//128)*128`). Se parte de la FAT1 real y se pisan esas páginas con las de los volcados. Ejemplo completo: `ejemplos_caso/buildfat.py`.
3. **Probar**: seguir cada cadena desde el cluster inicial de su entrada y comparar su largo con `ceil(tamaño/cluster)`. Si coinciden en las fotos de la zona sana y fallan en la dañada, el problema está en la zona dañada (sección 5), no en la reconstrucción.

La FAT reconstruida no siempre alcanza para rearmar el archivo en el orden correcto (en la tarjeta de referencia describía la asignación lógica, pero los datos estaban físicamente desordenados); sirve igual para saber **qué cluster inicial y qué tamaño tiene cada foto**.

## 5. Cómo se escribieron los datos (unidades y pares)

Método para descubrirlo sin suponer:

1. Con el período `P` de `mapa_clusters.py`, y la ubicación de los saltos fuertes, se obtiene una **grilla** de unidades de `P` clusters con un ancla (el cluster donde empieza la primera unidad).
2. Para cada unidad calcular el costo de empalme entre su cola y la cabeza de las unidades cercanas. Si el costo bajo no aparece con la unidad física siguiente sino con otra, hay reordenamiento. En la tarjeta de referencia las unidades iban **intercambiadas de a pares**: la unidad lógica `k` estaba en la posición física `k XOR 1` (`ejemplos_caso/unswap.py`, `pieces.py`).
3. **Después de un volcado de metadatos la grilla se corre**: la unidad que lo contiene queda más corta (en la de referencia, 376, 379 y 377 clusters en vez de 383, una por volcado). Hay que re-detectar el borde con el salto fuerte más cercano (`ejemplos_caso/flujo.py`).
4. **Prueba del largo** (la más contundente): armar el flujo lógico (unidades en orden), ubicar las cabeceras TIFF y medir la distancia entre cabeceras consecutivas. Debe ser igual a `ceil((so + sc + 1024) / cluster)` de cada foto (`so` y `sc` salen del SubIFD de la propia cabecera). Cuando coinciden, el modelo es correcto; la que no coincide suele ser una foto que perdió datos (en la de referencia, 19 de 20 coincidieron y la que no era la última, cortada por la cámara).

Si el costo de empalme no muestra ningún patrón de reordenamiento, el problema es otro (archivos fragmentados normales: seguir la FAT; o datos sobrescritos: sección 7).

## 6. Armar los archivos

Con la ubicación lógica de cada foto, leer sus clusters en orden (`ejemplos_caso/reconstruir.py` arma `REARMADOS/DSCxxxxx.ARW` en bruto). Para las fotos que la FAT reconstruida sí describe bien, `ejemplos_caso/entregar_intactas.py` lee por cadena, **verifica** y separa en `OK` / `REVISAR`:

- cabecera `II*\0`;
- 6376 filas de tira presentes;
- cero saltos sospechosos en la continuidad fila a fila (`raw_cost_profile`: una fila cuyo costo contra la fila `r-2` es más de 6 veces el de su entorno y mayor a 300 es un salto);
- `fin de la tira + 1024 == tamaño del directorio`.

Una cabecera pisada por un volcado (la foto empieza con basura de FAT): reconstruirla con la de una foto vecina de la misma ráfaga (`rehacer_cabecera.py`) y comprobar que la tira queda alineada (`so = tamaño − 1024 − tira`).

## 7. Pérdidas en vuelo y huecos

En cada evento de volcado, el archivo que se estaba escribiendo **pierde datos**: quedan clusters con contenido viejo o ceros. En la tarjeta de referencia: huecos de 47 y 57 clusters en dos fotos, uno de 674 clusters (36 % de la imagen) en otra, pérdidas en la punta de dos más (11 % al principio, 9,5 % al final) y el último archivo truncado por el apagado de la cámara. Cómo se detecta: saltos de costo sin contrapartida, filas con datos ajenos, o una cadena `CORTO`.

Cómo se repara (solo en ARW): se localiza el hueco en filas (`r0 = (inicio_del_hueco - so) // 9600`, `r1 = ceil((fin - so) / 9600)`), y `sintesis.fill_rows(buf, so, jpeg, r0, r1)` rehace esas filas desde el JPEG de cámara del mismo archivo (o de otra copia sana; ver `reparar4.py`). Después revisar con ojos: lado a lado contra el JPEG, y especialmente la **costura** entre filas reales y sintéticas. Resultado: filas con calidad JPEG dentro de un RAW válido. **Se rotula y se informa el porcentaje.**

Si faltan los primeros clusters (nunca grabados, ceros), no hay cabecera ni tira superior: `huerfano.py` / `ejemplos_caso/borradas.py` arman cabecera con la de una foto de la misma ráfaga, tira real desde donde existe y filas negras arriba (válidas para el decodificador). Es `PARCIAL`: decir cuánto falta.

## 8. Borradas, huérfanas, fragmentos viejos

- **Borradas**: la entrada de directorio ya no existe y la FAT quedó en cero, pero cabecera y datos suelen seguir ahí. `carve_cabeceras.py` lista las cabeceras alineadas a cluster con estado `LIBRE` y las extrae como archivos contiguos de tamaño exacto (TIFF + relleno). Contiguo es una **suposición**: verificar (LibRaw, costos de empalme). Nombre: deducir por la numeración de los vecinos (`DSC01399 → 01400…`) y marcarlo; fecha de EXIF.
- **Fotos de otra cámara/otra sesión** al final de la tarjeta (en la de referencia, 8 NEF de 2021 contiguos): se extraen igual y se nombran por hora de disparo; el último puede estar truncado por el fin de la tarjeta.
- **Cobertura**: listar los trozos RAW que ningún archivo conocido reclama (`ejemplos_caso/orphans.py`). Lo que sobra suele ser restos de fotos más viejas sin cabecera: no son del evento; incluir como `EXTRA` solo si se pueden rearmar sin inventar.

## 9. Verificación en capas (de barata a cara)

1. Estructura TIFF: cabecera válida, fin de tira + relleno = tamaño esperado.
2. Continuidad fila a fila de la tira (sin saltos).
3. Decodifica con LibRaw (`rawpy.imread` + `postprocess`); un NEF cortado no decodifica ni con ceros.
4. Alineación de filas contra el JPEG embebido (`verificar_filas.py`, `ubicar_filas.py`): útil para saltos grandes y `AJENO`; **falsos positivos de ±6–7 filas en zonas planas**.
5. **Ojos**: hojas de contacto de todo (`ver_raw.py`, `hoja_contacto.py`, `jpgs_camara.py`); las reparadas, lado a lado con el JPEG de cámara. Las comparaciones automáticas RAW-vs-JPEG resultaron poco confiables.
6. SHA-256 de todo lo entregado.
