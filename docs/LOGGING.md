# Logs de outbound-spam

Desde 1.1.0 el instalador y `--upgrade` crean `/var/log/outbound-spam.log`, propiedad de root y modo **0600**, y `/etc/logrotate.d/outbound-spam`.

Cada entrada es una línea: fecha/hora local con zona y desplazamiento UTC, seguida de un objeto JSON con `tipo` (`manual` o `servicio`), `pid`, `evento` y sus detalles. Los saltos de línea de los mensajes se escapan para conservar una entrada por línea.

```text
2026-10-31 09:30:00 CET (UTC+01:00) {"tipo":"servicio","pid":1234,"evento":"estado","estado":"Spam Status: OK; incidentes=0; retenidos=0; bloqueadas=0","incidencias":[],"nuevos_incidentes":0,"bytes_leidos":0,"duracion":0.012}
```

## Qué se registra

- **Servicio:** inicio/parada y un resumen por pasada, con estado, incidentes, retenciones, bloqueos, problemas de cobertura/configuración y métricas breves de lectura.
- **Incidencias:** identificador, cuenta SASL, pico, umbral efectivo, ventana y ajustes de bloqueo, HOLD, contraseña y avisos. Se registra el detalle al aparecer o cambiar, evitando repetirlo sin cambios en cada pasada.
- **Acciones:** resultados de bloqueo SMTP, nuevas retenciones, cambio de contraseña y avisos; acción y error si falla. Los avisos siguen respetando las reservas y el mínimo de seis horas. Un ajuste desactivado aparece como `false` en el detalle de la incidencia.
- **Operaciones manuales:** solicitud, parámetros relevantes, cuenta, resultado y código. Las operaciones de cola completadas incluyen cuenta, ID de cola y liberación, reencolado o borrado. El resultado administrativo explica qué restricciones, incidentes o retenciones se conservan.
- **Instalación/upgrade:** versión, destino, respaldo, migración, resultado y reanudación del servicio. En el primer upgrade desde 1.0.x el registro comienza cuando entra el código candidato; el finalizador registra la terminación de la migración y el estado observado del servicio.
- **Errores:** fallos operativos y uso de la última configuración válida. Un error de escritura del log se informa por stderr/journal sin convertir una acción ya aplicada en una acción fallida.

No se vuelcan la configuración privada, contraseñas, hashes, SQL ni el contenido de los mensajes de correo. La salida administrativa capturada está limitada a 8 KiB por operación. `--help` y `--version` son consultas informativas sin apertura del estado ni del log; el comprobador ligero de Monit lee únicamente el resumen atómico. Las reproducciones `--replay` mantienen su caché en el directorio offline y no escriben en el log operativo.

## Consulta

```bash
sudo tail -n 50 /var/log/outbound-spam.log
sudo tail -f /var/log/outbound-spam.log
sudo zless /var/log/outbound-spam.log-*.gz
```

Para seguir una cuenta, buscar su dirección SASL y los identificadores de sus incidentes. Las entradas `estado` permiten situar las acciones dentro del estado general de cada pasada; el `pid` agrupa las entradas de una ejecución.

## Rotación instalada

```text
/var/log/outbound-spam.log {
        weekly
        missingok
        rotate 8
        compress
        delaycompress
        notifempty
        copytruncate
        dateext
}
```

La tarea de logrotate del servidor aplica esta política semanal: conserva ocho rotaciones, añade la fecha, comprime las antiguas y retrasa la compresión de la más reciente. `copytruncate` conserva el archivo abierto; además el programa abre/cierra el log en cada entrada. Como en cualquier política `copytruncate`, existe una pequeña ventana de pérdida de escrituras durante la copia/truncado.

La instalación crea la política; su ejecución periódica corresponde al cron/timer de logrotate del servidor. Para revisar su interpretación sin rotar:

```bash
sudo logrotate --debug /etc/logrotate.d/outbound-spam
```
