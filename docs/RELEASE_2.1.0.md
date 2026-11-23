# Release estable 2.1.0

## Cambios

- **Actualización automática de releases estables superiores**, aproximadamente cada hora. `auto_update=true`, `auto_update_interval=3600`.
- **Bloqueo por exceso de rechazos externos**: se abre incidente cuando `2 * rechazos > max_sent` en la ventana de la cuenta. Usa `custom_limits` cuando exista. Con límite 100, dispara al rechazo 51; con 101, también al 51; con 1, al primero.
- El contador nuevo es operativo/persistente e independiente del dashboard. Solo incluye intentos con identidad SASL comprobada o cola previamente atribuida y destinatario clasificado como externo. No suma rechazos a los envíos aceptados ni cuenta locales, anónimos, ignorados o destinos desconocidos. Los errores de clasificación son visibles y se reintentan.
- Las acciones existentes mandan: `block_smtp`, `block_local`, `hold_queue`, `notify` y **`reset_password`** conservan su significado. Un incidente por rechazos también cambia la contraseña de la cuenta SASL si `reset_password=true`. El mecanismo de bloqueo debe estar instalado y su acción habilitada, igual que para exceso de envíos.
- Motivo `external_rejections`, muestras del log, umbral y contador visibles en estado e informes. El dashboard mantiene la distinción entre envíos y rechazos.
- El empaquetador verifica en cada construcción que se completen todas las variables del ejemplo en producción, sin alterar los valores existentes ni duplicarlos en ejecuciones sucesivas.

## Upgrade en producción

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
systemctl is-active outbound-spam.service
systemctl is-enabled outbound-spam-update.timer
systemctl is-active outbound-spam-update.timer
systemctl list-timers outbound-spam-update.timer
```

Versión esperada: **2.1.0**. El código candidato instala y activa el timer durante la preparación incluso cuando lo invoca el actualizador publicado 1.3.x o 2.0.0. Reanuda el analizador si estaba activo; si estaba parado conserva ese estado. No necesita `--install-service` adicional. El primer chequeo se programa una hora después; el timer utiliza una unidad independiente para que el reinicio del analizador no interrumpa el upgrade.

El `.conf` de producción recibe las variables ausentes y el `.conf.example` se sincroniza. Una desactivación explícita de `auto_update` se conserva. No se modifican contraseñas, cola ni avisos durante la preparación; las acciones comienzan al reanudarse el analizador. No se reconstruyen rechazos antiguos desde el histórico estadístico: se empiezan a capturar desde el cursor operativo conservado.

Se conservan configuración, cuentas ignoradas, límites particulares, bloqueos, incidentes, reservas de avisos y estados de acciones. Python mínimo **3.8**, esquema operativo **4** (tabla aditiva), histórico **1**, API de upgrade/systemd **1**. Los paquetes mantienen la compatibilidad de componentes de 1.3.x.

Assets: `outbound-spam-2.1.0.tar.gz`, `antispam-isp-2.1.0.tar.gz` y `SHA256SUMS`.

## Validación reproducible

```bash
python3 tools/test_release210.py -v
python3 tools/test_config_completion.py -v
python3 tools/build_release.py --output dist/2.1.0
python3 tools/test_release200.py -v
```

La matriz de upgrades utiliza los paquetes oficiales 1.3.0–1.3.4 y 2.0.0 y ejecuta su CLI real contra assets locales, manteniendo validación y despliegue originales. En contenedores Focal/Python 3.8 y Noble/Python 3.12 se arranca el nuevo demonio/trabajador; un adaptador systemctl registra las operaciones y gestiona el proceso real. Se comprueban creación, habilitación y arranque del timer, conservación de valores, servicio activo/parado y ruta automática sin nueva release. Resultados finales en `TEST_RESULTS_2.1.0.json`.
