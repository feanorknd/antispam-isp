# outbound-spam 1.2.2

## Estado HTTP integrado

El propio `outbound-spam.service` atiende `GET /` y entrega el estado JSON completo en `http://127.0.0.1:23456/`. No necesita otro demonio para publicar el estado. La configuración permite cambiar dirección IPv4 y puerto:

```bash
status_http_host=127.0.0.1
status_http_port=23456
```

La respuesta es HTTP 200 incluso ante alertas o errores: NodePing HTTP Parse debe comprobar `code` o `http_metrics.code` con mínimo 0 y máximo 0. El código es 0 sano/calentamiento, 1 incidente/retención/bloqueo y 2 error, estado ausente o atrasado. La antigüedad se calcula en cada petición.

El JSON conserva cuentas y detalles y añade contadores numéricos en `http_metrics`: código, antigüedad en segundos, calentamiento 0/1, incidentes, mensajes retenidos, cuentas bloqueadas y errores. Los otros valores numéricos pueden consultarse mediante las rutas de HTTP Parse. Los textos no admiten reglas de rango numérico.

El listener usa HTTP y por defecto solo escucha en loopback. La documentación incluye un proxy Nginx para `/spam-status`. Cambiar host/puerto requiere reiniciar el servicio. Si el puerto está ocupado, el demonio falla al arrancar.

## Ejecución y upgrade

La ejecución operativa requiere el demonio. Se eliminan las opciones de instalación/desinstalación de cron y su variable de configuración. `--run` queda reservado al trabajador interno del servicio; las operaciones administrativas y replay siguen disponibles.

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
curl -fsS http://127.0.0.1:23456/
```

La versión debe mostrar **1.2.2**. La preparación del upgrade añade al final del archivo privado las variables HTTP ausentes y conserva los valores existentes. Se conservan SQLite, incidentes, reservas de avisos, bloqueos y retenciones. El servicio vuelve a arrancar si estaba activo; si estaba detenido o no estaba instalado, se requiere iniciarlo o instalarlo con `--install-service`. Retirar las programaciones antiguas del servidor.

Se conservan esquema SQLite 3, `upgrade_api=1` y `service_unit_api=1`.

## Validación

- Focal/Python 3.8 y Noble/Python 3.12: **187 pruebas de integración y 9 pruebas HTTP correctas por entorno**.
- Upgrades con el código real de los releases 1.0.0, 1.0.2, 1.1.0, 1.2.0 y 1.2.1; servicios activos y detenidos.
- Primera pasada del demonio real, respuesta HTTP completa y cierre por SIGTERM.
- JSON inválido, estado atrasado, peticiones concurrentes, puerto ocupado, configuración inválida e idempotencia de la migración.
- Sintaxis Bash, ShellCheck y validación reproducible de paquetes/manifiestos.

Assets: `outbound-spam-1.2.2.tar.gz`, paquete compatible `antispam-isp-1.2.2.tar.gz` y `SHA256SUMS`.
