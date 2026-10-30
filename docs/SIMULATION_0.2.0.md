# Reproducción con la política 0.2.0

Archivo: `mail.log-20261025.gz`, del 18 al 25 de octubre de 2026. Motor 0.2.0, límite base **100 destinatarios externos/hora**, dominio remitente distinto **25/h**, avisos por cuenta con mínimo **seis horas** y sin reconocimientos manuales.

El método y las limitaciones de reconstrucción del spool son los del [análisis inicial](SIMULATION_20261025.md). Se mantienen los mismos mensajes aceptados: no se inventa cómo habría cambiado el tráfico si se hubieran activado bloqueos. Tampoco se recalculan los tiempos de entrega históricos como si el nuevo freno SMTP ya hubiera estado instalado.

Las cuentas se anonimizan aquí como A/B/C, en el mismo orden del informe anterior. Sus nombres, IPs y ejemplos están en el resultado privado.

## Detección con cola reconstruida

| Cuenta | Primera alerta, Madrid UTC+02 | Externos al detectar | Límite efectivo | Motivo |
|---|---|---:|---:|---|
| A | 18/10 15:59 | 106 | 100 | Volumen |
| B | 21/10 06:34 | 27 | 25 | Dominio remitente distinto |
| C | 21/10 06:34 | 26 | 25 | Dominio remitente distinto |

B y C se detectan **21 minutos antes** que en 0.1.1, donde la primera alerta era a las 06:55. No aparecen cuentas adicionales con la configuración general de la prueba.

En el historial original todavía quedarían pendientes 98, 14 y 13 mensajes respectivamente. Son estimaciones del spool histórico, no una promesa de interceptar todas las entregas ni una predicción exacta con un ritmo de salida diferente.

## Comparación si no se completa la información desde la cola

| Cuenta | Con cola | Solo líneas de entrega disponibles | Retraso adicional |
|---|---|---|---:|
| A | 18/10 15:59 | 18/10 16:46 | 47 minutos |
| B | 21/10 06:34 | 21/10 06:46 | 12 minutos |
| C | 21/10 06:34 | 21/10 06:48 | 14 minutos |

Con la penalización por dominio, B y C acaban alertando incluso en la pasada limitada al log, pero más tarde. La lectura de destinatarios pendientes mantiene la ventaja de detectar la acumulación antes de que Postfix consiga darles salida.

## Informes

Se generan **56 avisos en toda la semana**, frente a los 333 de la política inicial:

- A: 26.
- B: 15.
- C: 15.

Son **tres informes iniciales y 53 recordatorios**, todos separados por cuenta. No se agrupan varias cuentas en un mismo correo. El mínimo entre intentos por cuenta es de seis horas, incluso si se reinicia el script o se reconoce y reabre el incidente.

La cifra presupone que nadie reconoce los incidentes durante la semana. Un reconocimiento administrativo detiene los recordatorios de ese incidente; la reserva temporal por cuenta se conserva.

## Resultados privados

```text
var/simulations/20261025-v0.2.0/summary.json
var/simulations/20261025-v0.2.0/reconstructed-queue/result.json
var/simulations/20261025-v0.2.0/reconstructed-queue/notifications/
var/simulations/20261025-v0.2.0/observed-log-only/result.json
```

La prueba reproduce 10.081 ejecuciones por escenario. Los directorios y los archivos con datos de correo reales siguen excluidos de Git. Las recomendaciones de máximos y periodos se encuentran en [POSTFIX.md](POSTFIX.md).
