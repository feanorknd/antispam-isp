# antispam-isp 1.0.2

## Cambios

- El calentamiento inicial es informativo y no genera error de Monit. No se ocultan incidentes ni fallos reales.
- `--status`, los demás comandos y los informes presentan fechas humanas con el timezone del servidor.
- `--help` explica cada comando e incluye ejemplos sencillos de diagnóstico, contención, HOLD, purga y resolución de falsos positivos.
- Los enlaces al lanzador funcionan desde cualquier directorio, incluidos `/usr/local/sbin/outbound-spam`, enlaces relativos y cadenas.
- El actualizador de esta versión puede renovar unidades systemd propias intactas, con respaldo y `daemon-reload`, conservando ExecStart, drop-ins y estado activo/parado. Preserva los archivos base personalizados.

## Aplicación

Usar la ruta absoluta si el enlace del lanzador antiguo falla:

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
/root/scripts/outbound-spam.sh --status
```

Tras una pasada nueva, el calentamiento ya devuelve código 0 salvo otro problema. La configuración operativa y el estado se conservan.

La primera actualización desde 1.0.1 usa el actualizador antiguo, que actualiza el programa y reanuda el servicio activo. La definición operativa ya coincide con esta versión; para sincronizarla expresamente puede ejecutarse `--install-service`. Las actualizaciones posteriores con 1.0.2 incluyen revisión automática de la unidad cuando cambia la plantilla.

El instalador sigue solicitando únicamente `postfix-sqlite` con `--install-deps`. Se mantiene el esquema SQLite 3.
