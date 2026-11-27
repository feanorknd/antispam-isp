# Release estable 2.1.1

- **Margen de 4 minutos (240 segundos)** cuando `postcat` no encuentra el fichero de cola de un mensaje aceptado. Se guarda la primera observación en SQLite y se registra `cola_consulta_pendiente` en el log. Si los datos se completan a tiempo, ese fallo y el aviso asociado no afectan al estado; si siguen pendientes, aparecen en la primera pasada a partir del vencimiento. Los errores de permisos, los incidentes de spam, las retenciones y los bloqueos mantienen su tratamiento inmediato.
- **Borrado administrativo por ID**: `postfix/postsuper[PID]: ID: removed` en el log monitorizado marca la ficha como retirada, conserva la evidencia y cierra el pendiente. No registra una entrega ni reconoce el incidente de spam. Un resumen de borrado masivo sin IDs no basta para confirmar qué mensajes se retiraron.
- **Mismo contrato de estado**: `issues`, `code`, `http_metrics.issues` y `http_metrics.code` se mantienen. No hay que cambiar NodePing o Monit.

## Upgrade en producción

Los servidores con 2.1.0 y el timer de actualización habilitado detectan este release en su siguiente comprobación. Para actualizar manualmente:

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
systemctl is-active outbound-spam.service
```

Durante la preparación se crea `queue_missing` y se añade `messages.admin_removed` si faltan, sobre un respaldo previo del actualizador. La migración es aditiva e idempotente y se conservan mensajes, cursores, incidentes, bloqueos, configuración y el estado activo/parado del servicio. No se requiere SQL manual ni relectura del log. Se mantienen Python ≥ 3.8, esquema operativo 4 y API de upgrade/systemd 1.

Assets: `outbound-spam-2.1.1.tar.gz`, `antispam-isp-2.1.1.tar.gz` y `SHA256SUMS`.

## Validación

Las regresiones de cola prueban el vencimiento exacto, el reinicio, la resolución por el log, el borrado confirmado y el estado de spam simultáneo. En contenedores Ubuntu 20.04 y 24.04 se comprobó el diagnóstico real de `postcat`, la línea real que escribe `postsuper` y el upgrade desde el paquete publicado 2.1.0 con una base poblada, tanto automático con servicio activo como manual con servicio parado y fallo inducido después de preparar la migración.
