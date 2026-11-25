# Entrega: carpeta final, reporte y mensaje al usuario

El usuario va a abrir la carpeta, mirar una hoja de contacto y reenviar fotos a clientes. Lo que más pesa es que **sepa qué es cada archivo** y no tenga sorpresas. Todo esto se arma al final, cuando las fotos ya pasaron la verificación (`forense.md`, sección 9).

## Estructura

`RECUPERADO_FINAL` va dentro de la carpeta del rescate, `sd-raw-rescue\AAAA-MM-DD\` (ver "Dónde va todo" en `SKILL.md`), nunca suelta en la raíz del disco.

```
RECUPERADO_FINAL/
  RAW_<Marca>_<EXT>/          fotos RAW con su nombre (original o deducido, ver reporte)
  JPG_camara/                 un JPG por foto: el de la cámara si está sano; si no, revelado del RAW (render_jpg.py)
  RAW_<otra cámara>_<año>/    otra sesión que estaba en la tarjeta, con su JPG
  EXTRA_<descripción>/        restos sin origen claro, solo si sirven; con su propio LEEME
  HOJA_DE_CONTACTO_N.jpg      miniaturas de todo (unas 20 por hoja), con el nombre
  REPORTE.csv                 una fila por archivo
  LEEME.txt                   texto para humanos
  SHA256.txt                  huellas de todo (menos de sí mismo)
  herramienta/                RESCATAR_TARJETA.bat + rescatar_sd.py + LEEME
```

`REPORTE.csv`: separador `;`, UTF-8 con BOM (para que Excel lo abra bien). Columnas usadas: `archivo; estado; tam_MB; fecha_camara; nombre; filas_rehechas; pct_rehecho; detalle; origen; jpg`.

## Estados (y su definición)

- **COMPLETA**: los bytes salieron de la tarjeta tal cual, sin retoque; pasó la verificación.
- **REPARADA**: se rehicieron filas desde el JPEG, se corrigieron filas sueltas de empalme o se reconstruyó la cabecera. El `detalle` dice qué y cuánto (`pct_rehecho`).
- **PARCIAL**: falta una parte que no se puede rehacer (principio sin grabar, tarjeta terminada). Decir qué falta.

El estado y los porcentajes se escriben al armar cada foto, en un diccionario con **el mismo criterio de clave** que se usa para recorrerlo (un desfasaje de claves dejó una vez las 58 fotos como `COMPLETA`): al terminar, contar estados y comparar contra lo que se sabe.

## Reglas de armado

- Origen de cada archivo por prioridad: versión más trabajada primero (en el caso: `BORRADOS` > `REPARADOS` > `RAW_RECUPERADOS` > `REARMADOS`); afirmar que cada foto sale de una sola carpeta y que no falta ninguna del rango esperado.
- Fecha de modificación = `DateTimeOriginal` del EXIF (`os.utime`), así el explorador ordena por hora de disparo.
- Nombre deducido por numeración o por hora de disparo → columna `nombre` lo dice; el LEEME también.
- JPG: decidir mirando las hojas de contacto de los JPG de cámara cuáles están rotos (`jpgs_camara.py`); los rotos o ausentes se generan del RAW. No usar comparaciones automáticas RAW-vs-JPEG: fallaron.
- Hojas de contacto de los RAW reparados lado a lado con el JPEG de cámara, para ojo humano.
- `SHA256.txt` al final, sobre la carpeta ya terminada.
- Dejar `herramienta/` para que el usuario pueda repetir el diagnóstico en otra tarjeta sin Claude.

## Plantilla de `LEEME.txt`

Texto llano, sin jerga, para quien no sabe qué es un cluster. Ejemplo (completar los `<…>`):

```
RECUPERACION DE LA TARJETA SD - "<EVENTO>" (<FECHA>, <CAMARA>)
==============================================================

QUE HAY EN ESTA CARPETA
  RAW_<...>\        <N> fotos RAW: <primera> a <ultima>. Son TODAS las que quedaron en la tarjeta.
  JPG_camara\       el JPG de cada una (ver nota abajo).
  HOJA_DE_CONTACTO_*.jpg   miniaturas para mirarlas de un vistazo.
  REPORTE.csv       estado de cada archivo, una linea por foto (abrir con Excel).
  SHA256.txt        huellas digitales de todos los archivos (para comprobar copias).
  herramienta\      programa para repetir el rescate en otra tarjeta.

ESTADO DE LAS FOTOS (detalle en REPORTE.csv)
  COMPLETAS (<n>): recuperadas enteras, byte a byte, sin ningun retoque: <numeros>
  REPARADAS (<n>): a la tarjeta le faltaban pedazos y se completaron: <numeros>
  PARCIAL   (<n>): <numeros>

LO QUE TENES QUE SABER DE LAS REPARADAS
  * Los pedazos que la tarjeta no guardo se rehicieron desde el JPEG que la camara metio dentro de cada RAW.
    Esas filas tienen calidad de JPEG (8 bits, con el revelado de la camara), no de RAW real. El resto es RAW original.
  * Fotos con mas relleno: <foto> (<x>% de la imagen, <parte>), ...
  * <fotos con cabecera prestada, nombres deducidos, JPG generados desde el RAW, RAW truncados>

QUE LE PASO A LA TARJETA (resumen)
  <2-4 lineas sin jerga: que se escribio mal, que se leyo solo en lectura, que no conviene seguir usandola>

NO INCLUIDO
  <lo que quedo fuera y se puede hacer a pedido: video, fotos viejas sueltas>
```

## Mensaje de cierre en el chat

Corto y en el idioma del usuario (en el caso de referencia, español rioplatense directo): carpeta; conteo por estado; las reparadas con más relleno y qué significa (calidad JPEG en esas filas); lo parcial; nombres deducidos; qué no se verificó (apertura en Lightroom/Photoshop/Imaging Edge si solo se probó LibRaw); que la tarjeta no se tocó y que conviene no usarla para trabajo; qué quedó afuera y que se puede hacer a pedido. No ofrecer borrar la imagen de 32 GB sin que el usuario haya validado: preguntar antes.
