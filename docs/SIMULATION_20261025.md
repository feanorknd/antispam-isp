# Simulación de `mail.log-20261025.gz`

**Resultado histórico del motor 0.1.1.** La política actual, con umbral por dominio remitente y avisos cada seis horas, está en [SIMULATION_0.2.0.md](SIMULATION_0.2.0.md).

Análisis realizado el **2026-10-29**, motor **0.1.1**. Este documento anonimiza las cuentas; los nombres, IPs, destinatarios y muestras completas están en los resultados privados del workspace.

## Archivo y configuración

- Periodo: **18/10/2026 00:00:03 — 25/10/2026 00:00:00**, Europe/Madrid, UTC+02.
- 428.953 líneas; 8.576.535 bytes comprimidos y 72.264.652 descomprimidos.
- 9.540 IDs atribuidos a cuentas autenticadas; 8.332 mensajes aceptados y 1.208 sin aceptación.
- 8.649 eventos de destinatarios externos y 966 locales.
- Ningún destinatario aceptado sin clasificación o sin correspondencia con `nrcpt` al completar el archivo.
- Límite: **más de 100 destinatarios externos en 3.600 segundos**.
- Sin cuentas ignoradas ni límites particulares reales.
- Avisos activados y recordatorios cada hora. No hay reconocimientos manuales.
- Bloqueo, HOLD y cambio de contraseña desactivados, como en el ejemplo distribuido.

## Método

Se avanza el reloj virtual de minuto en minuto y se añaden al log activo solo las líneas que ya existirían en ese instante. Cada ejecución conserva el cursor, los eventos, incidentes y acciones en SQLite. Se reproducen **10.081 ejecuciones** por escenario.

### 1. Cola reconstruida

Un censo previo relaciona cada mensaje con los destinatarios finalmente registrados y las fechas de aceptación y finalización. En cada minuto, la cola virtual expone únicamente mensajes ya aceptados y todavía pendientes; excluye los destinatarios cuya entrega o rebote ya terminó.

Los datos posteriores se usan para reconstruir **metadatos de sobres**, no para anticipar mensajes futuros. Esta es una aproximación retrospectiva a lo que habría leído `postcat`: el archivo no contiene capturas reales del spool ni permite probar que un HOLD habría interceptado una entrega ya en curso.

### 2. Solo información del log disponible

La misma secuencia temporal y el mismo motor, sin consultar la cola. Los destinatarios se conocen cuando aparecen sus líneas de entrega y se asignan a la fecha de aceptación original del mensaje.

No es una afirmación de que cualquier analizador posible de logs deba comportarse así: es una comparación del motor implementado con y sin metadatos de cola.

### Acciones y avisos

Se utiliza la máquina real de incidentes/notificaciones, sustituyendo el transporte por escritura privada de informes. No se ejecuta ningún comando Postfix, MySQL ni envío SMTP. Para acelerar la reproducción se omite la reescritura idéntica de informes entre notificaciones; se ejecutan las acciones al abrir incidentes y al vencer recordatorios. Por ello el tiempo del simulador no es un benchmark integral de todas las operaciones de producción.

## Incidentes con cola reconstruida

| Cuenta anonimizada | Primera alerta | Externos al detectar | Máximo en una hora | Externos en la semana | Mensajes aún pendientes estimados |
|---|---|---:|---:|---:|---:|
| A | 18/10, 15:59 | 106 | 106 | 305 | 98 |
| B | 21/10, 06:55 | 103 | 267 | 3.650 | 69 |
| C | 21/10, 06:55 | 102 | 267 | 3.644 | 70 |

Las identidades correspondientes figuran en `summary.json` e informes de notificación privados.

### Evidencias observadas

- A incluye un envío muy rápido de 102 mensajes desde una misma IP y asuntos de verificación de perfil, método de pago y un servicio de peaje. Su máximo exacto en 60 segundos es 100 destinatarios, en 300 segundos es 102 y en una hora es 106.
- B y C comparten una IP predominante con **3.643 mensajes aceptados por cuenta**. Se observan mensajes repetidos de facturación alemana, con remitente ajeno a las cuentas autenticadas.
- Son indicios fuertes de abuso compatibles con spam/phishing, adicionales al exceso de volumen. El log no permite inspeccionar cuerpos ni confirmar el contenido de posibles adjuntos.
- Las otras cuentas quedan por debajo del umbral: los siguientes máximos horarios son 74 y 72 destinatarios. No superar un límite de volumen no equivale a certificar que todo su contenido sea legítimo.

## Diferencia sin metadatos de cola

Solo se abre el incidente A, a las **16:46 del 18/10**, con 101 destinatarios reconocidos dentro de la ventana: **47 minutos más tarde**. En ese momento quedarían cuatro mensajes pendientes según la reconstrucción.

Los incidentes B y C no se abren en esa pasada. El retraso de entrega impide que el motor conozca a tiempo todos los destinatarios de los mensajes aceptados en la ventana. Esto confirma la necesidad de consultar el spool para cumplir el objetivo de detectar acumulaciones antes de su salida.

## Monit y número de avisos

Con cola reconstruida:

- 60 ejecuciones en código 2 durante el calentamiento inicial.
- 898 ejecuciones en código 0.
- 9.123 ejecuciones en código 1, desde la primera anomalía hasta el final: los incidentes quedan abiertos sin `--ack`.
- Ningún atraso por superar `max_read_bytes`.

Se generarían **333 avisos**: tres iniciales y 330 recordatorios. A generaría 153, B 90 y C 90. **No son 333 incidentes independientes.** El reconocimiento administrativo detendría los recordatorios del incidente revisado.

La pasada sin cola genera 152 avisos del único incidente. La generación del correo no equivale a recepción: con `alert_transport=sendmail` también dependería de la cola de salida real.

## Sensibilidad de los límites

Según los máximos deslizantes reconstruidos:

| Límite | Cuentas que lo superarían |
|---|---|
| 100 / 3.600 segundos | A, B y C |
| 100 / 300 segundos | Solo A |
| 100 / 60 segundos | Ninguna: A alcanza exactamente 100, no más |
| 300 / 3.600 segundos | Ninguna |

El envío sostenido de B/C demuestra por qué un máximo por minuto relativamente alto puede dejar pasar mucho abuso diario. No se ha modificado el límite de ejemplo como consecuencia del análisis.

## Corrección descubierta durante la prueba

La versión 0.1.0 conservaba como pendientes algunos IDs asignados a sesiones abandonadas antes de aceptar DATA. Eso podía dejar Monit en error de cobertura durante días, aunque esos mensajes no consumieran cuota.

La versión 0.1.1:

1. Sigue conexión y PID, conservando el estado entre lecturas.
2. Cierra transacciones con resumen de desconexión sin DATA/BDAT aceptado.
3. Permite un `qmgr` retrasado cuando sí hubo DATA/BDAT correcto.
4. No considera un error operativo el mero hecho de que una transacción no haya sido aceptada todavía.

La reproducción final elimina el error permanente y deja como único código 2 del escenario reconstruido el calentamiento inicial. Las tres detecciones de abuso mantienen las mismas horas y volúmenes.

## Resultados privados y reproducción

En el workspace de análisis:

```text
var/simulations/20261025-final/summary.json
var/simulations/20261025-final/census.sqlite3
var/simulations/20261025-final/reconstructed-queue/result.json
var/simulations/20261025-final/reconstructed-queue/notifications/
var/simulations/20261025-final/observed-log-only/result.json
```

Esos archivos contienen datos reales de correo y están excluidos de Git. Los directorios de simulación tienen modo 0700 y los archivos privados 0600. Las primeras reproducciones de depuración se conservan en otros directorios de `var/simulations`; **el resultado final es `20261025-final`**.

Para repetir, elegir un directorio nuevo:

```bash
python3 tests/simulate_production.py var/log/mail.log-20261025.gz \
  --output var/simulations/otra-reproduccion \
  --max-sent 100 --time-period 3600 --timezone Europe/Madrid
```

`--resume` permite continuar una ejecución interrumpida con el mismo archivo, versión y parámetros. Para cambiar límites o motor, utilizar otro directorio.

La reproducción final tardó unos 276 segundos para los dos escenarios y el censo. El máximo de trabajo de una ejecución virtual fue inferior a 0,25 segundos en el equipo de análisis. No se simula que un bloqueo altere el tráfico futuro: el historial continúa para poder comparar las dos observaciones sobre los mismos datos.
