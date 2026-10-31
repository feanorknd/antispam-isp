# Instalación del release y actualización

Repositorio público: **https://github.com/feanorknd/antispam-isp**.

## Instalación nueva desde el release 1.1.0

En un servidor Debian/Ubuntu con Postfix/ISPConfig configurados:

```bash
curl -fLO https://github.com/feanorknd/antispam-isp/releases/download/v1.1.0/outbound-spam-1.1.0.tar.gz
curl -fLO https://github.com/feanorknd/antispam-isp/releases/download/v1.1.0/SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf outbound-spam-1.1.0.tar.gz
sudo ./outbound-spam-1.1.0/install.sh --install-deps
```

`--install-deps` solicita a apt **únicamente `postfix-sqlite`**, sin paquetes recomendados. Python 3.8+ y Postfix deben estar instalados previamente; las demás herramientas utilizadas por las funciones que se activen se preparan fuera del instalador. Omitir la opción si el módulo ya está presente. El instalador no configura un servidor ISPConfig desde cero ni sustituye la configuración del MTA.

Por defecto instala en `/root/scripts`. Admite `--target /otra/ruta` y `--config /ruta/privada.conf`. En una instalación nueva crea una configuración privada con avisos desactivados y sin destinatarios ficticios ni excepción ficticia de boletín.

Editar esa configuración, establecer las direcciones reales y habilitar las acciones elegidas:

```bash
nano /root/scripts/outbound-spam.conf
/root/scripts/outbound-spam.sh --doctor
/root/scripts/outbound-spam.sh --install-postfix-guard
/root/scripts/outbound-spam.sh --configure-postfix
/root/scripts/outbound-spam.sh --install-service
/root/scripts/outbound-spam.sh --install-monit
systemctl status outbound-spam.service
```

El paquete incluye el instalador, los dos scripts, todos los módulos Python/Bash, ejemplo de configuración, manifiesto con hashes y documentación. No incluye pruebas, logs, bases de datos ni copias de servidores.

## Actualizar un servidor instalado

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
```

El comando consulta `/releases/latest` de la API pública de GitHub. Solo acepta releases estables con versión `X.Y.Z` o `vX.Y.Z`; no requiere token de GitHub. Si no hay una versión superior, lo indica y termina sin desplegar archivos.

Cuando hay una versión más moderna:

1. Descarga el paquete oficial y `SHA256SUMS` mediante HTTPS.
2. Verifica el SHA-256 antes de extraer y valida rutas, tipos de archivos y hashes internos. No extrae enlaces ni rutas que puedan salir del directorio de preparación.
3. Detiene la unidad propia si estaba activa, antes de tomar el bloqueo de operaciones. Un demonio manual activo debe detenerse expresamente.
4. Espera las operaciones manuales y guarda una copia privada del estado y del código anterior.
5. Ejecuta la preparación/migración de estado con el código candidato, sin publicar bloqueos, cambiar contraseñas, enviar avisos ni operar sobre la cola.
6. Activa la versión completa mediante un cambio atómico de `current` y actualiza los puntos de entrada propios.
7. Vuelve a iniciar el servicio únicamente si estaba activo.

Desde el actualizador 1.0.2 se revisa también la definición systemd existente. Una unidad propia intacta se actualiza con la plantilla del candidato si declara `service_unit_api=1`; se guarda su copia y se ejecuta `daemon-reload`. Se preservan `ExecStart` (incluidos config/ruta de estado), el estado parado/activo y los archivos `.service.d/`. Una unidad base personalizada se conserva y se informa de ello. Si falla `daemon-reload` antes del arranque nuevo se restaura la definición anterior junto con código/estado.

### Migración a 1.1.0 desde 1.0.x

El primer upgrade utiliza todavía el actualizador instalado. Por ello se publica también `antispam-isp-1.1.0.tar.gz`, un paquete de compatibilidad con el mismo código y manifiesto que el principal. El repositorio de GitHub conserva su URL histórica; el nombre operativo del programa y del paquete principal es **outbound-spam**.

La preparación de 1.1.0 traslada el almacenamiento de `/root/scripts/.antispam/` a `/var/lib/outbound-spam/`. Durante el cierre del actualizador antiguo se mantiene un alias transitorio; un finalizador privado espera su bloqueo y la limpieza de la descarga, y retira ese alias y la caché antigua automáticamente. Se registra `migracion_completada` en el log. El código nuevo y el servicio ya utilizan la ubicación nueva, incluso durante ese cierre. El traslado también contempla sistemas de archivos distintos para `/root` y `/var`.

El instalador de 1.1.0 realiza la misma migración de forma síncrona cuando se ejecuta sobre una instalación anterior. Si falla antes de arrancar el nuevo servicio, restaura las rutas anteriores junto con el estado y los puntos de entrada.

La configuración operativa se conserva byte por byte, sin cambiar su inode, fecha de modificación ni permisos. Tampoco se añaden automáticamente variables. Si el ejemplo nuevo incorpora nombres ausentes tanto en el ejemplo anterior como en la configuración privada, se notifican en `missing_new_variables` para revisión; esta detección de asignaciones Bash es orientativa y no ejecuta el archivo privado.

Una actualización no regenera una configuración desaparecida: se cancela antes de cambiar el programa. En una instalación nueva solo se crea la configuración si no existía. También se conservan el estado, reservas de avisos, retenciones, bloqueos, configuración de Postfix y decisiones de relay. Se actualiza el ejemplo y, si existe, el comprobador Monit gestionado.

## Distribución instalada

```text
/root/scripts/outbound-spam.sh
/root/scripts/outbound-spam.conf
/root/scripts/outbound-spam-status.sh
/root/scripts/outbound-spam.conf.example
/var/lib/outbound-spam/current -> releases/1.1.0
/var/lib/outbound-spam/releases/1.1.0/
/var/lib/outbound-spam/installation.json
/var/lib/outbound-spam/upgrade.lock
/var/lib/outbound-spam/upgrade-backup-*/
/var/lib/outbound-spam/outbound-spam.conf.last-good.json
/var/lib/outbound-spam/state.sqlite3
/var/lib/outbound-spam/status.json
/var/lib/outbound-spam/recovery/
/var/log/outbound-spam.log
/etc/logrotate.d/outbound-spam
```

Los módulos y documentación están dentro de la versión instalada. El lanzador fija `OUTBOUND_SPAM_BASE` para la configuración y `OUTBOUND_SPAM_CODE` para usar un conjunto coherente de módulos. Se conservan las versiones anteriores y las copias de actualización.

Directorios privados: root y modo 0700; scripts: 0700; configuración, estado, respaldos y log: 0600. La unidad systemd sigue ejecutándose como root y Monit conserva su comprobador gestionado. Los mapas de Postfix mantienen sus permisos específicos `root:postfix`/0640 en `/etc/postfix`, necesarios para las consultas del MTA. Los archivos ajenos presentes en el directorio de scripts se respetan. Un `state_dir` personalizado sigue siendo la ubicación del estado de esa instancia; el almacenamiento del programa y la caché están en `/var/lib/outbound-spam/`.

Instalación y upgrade crean el log y su rotador. Véase [LOGGING.md](LOGGING.md).

Una instalación plana anterior se convierte ejecutando `install.sh` del paquete nuevo. Se preserva el directorio `lib/` antiguo para no ocultar utilidades ajenas; el lanzador pasa a utilizar la distribución versionada. `--upgrade` requiere haber realizado esa instalación con gestor de releases.

## Fallos y recuperación de una actualización

Un fallo de descarga, checksum, validación o preparación conserva la versión anterior. Si todavía no se inició el nuevo servicio, se restauran el estado anterior y el punto de entrada y se intenta reactivar la unidad anterior.

Después de intentar iniciar el nuevo servicio no se rebobina automáticamente SQLite: el nuevo proceso podría haber enviado avisos o aplicado acciones. Si falla ese arranque se mantiene la versión preparada y su estado, se devuelve un error y se indica revisar `systemctl`/`journalctl`. Las copias privadas permiten una recuperación administrativa controlada.

Una operación manual que estuviera esperando durante el cambio de versión debe volver a ejecutarse: no se deja que código antiguo abra el estado después de activar un release nuevo.

El actualizador no sustituye la revisión de los cambios de versión. Las futuras distribuciones deben mantener el contrato `upgrade_api=1` y el modo de preparación; los paquetes incompatibles o sin los componentes requeridos se rechazan.
