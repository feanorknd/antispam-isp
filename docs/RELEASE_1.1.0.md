# outbound-spam 1.1.0

## Rutas y permisos

- `/root/scripts/`: únicamente `outbound-spam.sh`, `outbound-spam.conf`, `outbound-spam-status.sh` y `outbound-spam.conf.example` como componentes de la aplicación.
- `/var/lib/outbound-spam/`: código versionado, `current`, registro de instalación, bloqueos de upgrade, respaldos, estado y recuperación. La caché pasa a `/var/lib/outbound-spam/outbound-spam.conf.last-good.json`.
- Migración automática desde la distribución anterior, tanto con `install.sh` como con el `--upgrade` real de 1.0.x, conservando configuración, incidentes, bloqueos, HOLD y reservas de avisos.
- Servicio activo detenido/reanudado coordinadamente; una unidad parada sigue parada. Directorios privados 0700, scripts 0700, configuración/estado/log 0600. Se preserva el acceso específico de Postfix a sus mapas.

## Logs

- `/var/log/outbound-spam.log`, con fecha/hora y zona del servidor, tipo manual/servicio y PID.
- Resumen breve por pasada; detalles de incidencias al aparecer/cambiar; resultados de las acciones según la configuración.
- Operaciones administrativas, consecuencias, fallos, instalación y actualización trazables, sin credenciales ni contenido de correo.
- `/etc/logrotate.d/outbound-spam`: semanal, ocho rotaciones, compresión diferida, `copytruncate` y fecha.

## Actualizar

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --status
sudo tail -n 30 /var/log/outbound-spam.log
```

El primer actualizador antiguo conserva un alias de transición hasta terminar de limpiar la descarga. Se retira automáticamente y queda anotado como `migracion_completada`.

Paquete principal: **`outbound-spam-1.1.0.tar.gz`**. Se publica también un paquete con el nombre histórico para que los actualizadores antiguos lo encuentren. Ambos tienen checksums en `SHA256SUMS`. El repositorio público mantiene su URL.

Validación: **163 pruebas correctas en cada una de las cinco distribuciones**, con Postfix, Monit, demonio y logrotate reales; upgrade desde 1.0.2 con unidad activa/parada y entre sistemas de archivos distintos. Las llamadas a systemctl se simulan en los contenedores. Detalles en [TESTS.md](TESTS.md).

Se mantiene SQLite 3, Python 3.8+ y `upgrade_api=1`. Las dependencias solicitadas por `--install-deps` siguen limitadas a `postfix-sqlite`.
