# Instalación

## Opción recomendada: paquete del release

La distribución estable incluye `install.sh`, todos los módulos y el manifiesto de integridad. El instalador crea una distribución versionada, respeta la configuración existente y prepara `--upgrade` para versiones posteriores. Seguir [UPGRADE.md](UPGRADE.md).

Los pasos de copia manual siguientes se conservan para instalaciones planas anteriores. Para disponer del actualizador automático, ejecutar después `install.sh` del paquete oficial sobre esa instalación.

## 1. Dependencias

En un servidor Debian/Ubuntu con Python 3.8+ y Postfix ya instalados, el único paquete que instala la opción `--install-deps` es:

```bash
apt-get update
apt-get install --no-install-recommends -y postfix-sqlite
```

Se presupone una instalación funcional de Postfix y Monit. El cliente `mysql` se utiliza para la instantánea automática de rutas ISPConfig del bloqueo remoto y para el cambio de contraseña. Este último necesita además `openssl` y `doveadm`. Para SMTP directo con TLS, debe estar instalado `ca-certificates`.

`postfix-sqlite` permite que el bloqueo consulte tablas actualizadas en cada transacción SMTP, incluidas sesiones abiertas. Utiliza `proxymap` y clases de restricciones nativas. La base de contadores privada y el mapa legible por el usuario Postfix son distintos.

## 2. Copiar los componentes

Ejecutar desde el directorio de esta distribución, como root:

```bash
install -d -m 0700 /root/scripts
install -d -m 0700 /root/scripts/lib
install -m 0700 outbound-spam.sh /root/scripts/outbound-spam.sh
install -m 0700 outbound-spam-status.sh /root/scripts/outbound-spam-status.sh
install -m 0600 lib/outbound_spam.py /root/scripts/lib/outbound_spam.py
install -m 0600 lib/postfix_settings.py /root/scripts/lib/postfix_settings.py
install -m 0600 lib/runtime.py /root/scripts/lib/runtime.py
install -m 0600 lib/state_recovery.py /root/scripts/lib/state_recovery.py
install -m 0600 lib/distribution.py /root/scripts/lib/distribution.py
install -m 0600 lib/load_config.sh /root/scripts/lib/load_config.sh
install -m 0600 outbound-spam.conf.example /root/scripts/outbound-spam.conf.example
install -m 0600 outbound-spam.conf.example /root/scripts/outbound-spam.conf
```

En una actualización, conservar `outbound-spam.conf`. Desde 1.3.1, el instalador y `--upgrade` completan automáticamente todas las variables que falten respecto al ejemplo, preservando las asignaciones y valores existentes. Se guarda un respaldo privado y se valida la equivalencia antes de escribir. El ejemplo instalado se sincroniza con la distribución. Véase [UPGRADE.md](UPGRADE.md#configuración-completa-automáticamente-desde-131).

Editar `/root/scripts/outbound-spam.conf`, especialmente:

```bash
max_sent=100
time_period=3600
report=(admin@su-dominio.example segundo-admin@su-dominio.example)
alert_from=outbound-spam@su-dominio.example
ignore=()
custom_limits=()
```

Los límites anteriores son un ejemplo, no una recomendación de capacidad para todos los servidores. El array `custom_limits` del ejemplo de distribución contiene una dirección ficticia; sustituirlo o vaciarlo.

Los archivos de configuración deben pertenecer a root y tener modo `0600`. En ejecución privilegiada se verifican también los directorios ascendentes. La configuración es código Bash y no debe proceder de usuarios de correo.

## 3. Comprobar la configuración

Con el acceso SQL a ISPConfig disponible e instalando primero la ACL local predeterminada:

```bash
/root/scripts/outbound-spam.sh --version
/root/scripts/outbound-spam.sh --install-local-guard
/root/scripts/outbound-spam.sh --doctor
```

`--doctor` comprueba dependencias, carga de configuración de Postfix y disponibilidad de SQLite si se ha activado `block_smtp`. Los formatos concretos de contraseña y el registro ISPConfig se comprueban al ejecutar esa acción; la prueba completa contra la instalación real se registra en [TASKS.md](TASKS.md).

Para clasificar destinatarios pendientes, `routing_mode=auto` utiliza `postconf` y `postmap` sobre los mapas ya configurados. Con rutas o transportes especiales, consultar [ARCHITECTURE.md](ARCHITECTURE.md).

Desde 1.3.0, `--doctor` comprueba también `web_domain` y la ACL local si están activados `monitor_local`/`block_local` (ambos true por defecto). `--install-local-guard` valida y recarga Postfix. El cliente mysql y su acceso por socket deben estar disponibles. Para diagnosticar UID y dominios sin enviar correo: `--inspect-local-user web6 --sender cuenta@su-dominio.example`. Véase [LOCAL_SUBMISSION.md](LOCAL_SUBMISSION.md).

## 4. Instalar el bloqueo SMTP del plan A

```bash
/root/scripts/outbound-spam.sh --install-postfix-guard
```

El comando:

1. Crea `/etc/postfix/outbound-spam-access.sqlite3`, inicialmente vacía.
2. Crea `/etc/postfix/outbound-spam-access.cf`, sin contraseñas.
3. Antecede a las restricciones de destinatario existentes con:

   ```text
   check_sasl_access proxy:sqlite:/etc/postfix/outbound-spam-access.cf
   ```

4. Crea la clase `outbound_spam_remote` y la consulta `outbound-spam-access-recipient.cf`: devuelve `DUNNO` para locales y 451 para remotos. Añade las referencias necesarias a `proxy_read_maps`.
5. Comprueba sobrescrituras en `master.cf`, ejecuta `postfix check` y reinicia Postfix.
6. Guarda las restricciones previas y una copia de `main.cf` dentro del directorio privado de estado.
7. Con `monitor_local=true`, instala también la ACL hash de sendmail/postdrop, o verifica que ya esté instalada.

Las sobrescrituras por servicio incompatibles se notifican para que el administrador las adapte: no se reemplazan las restricciones específicas de otros servicios.

Después puede activarse en la configuración:

```bash
block_smtp=true
hold_queue=true
# Activación independiente:
reset_password=false
```

El bloqueo responde `451 4.7.1` a los destinatarios remotos de la cuenta SASL. Un destinatario puramente local continúa con las restricciones originales: no se fuerza `OK` ni se eluden validaciones de buzón/cuota. La recepción del buzón sigue disponible. Consultar las particularidades de mensajes mixtos en [OPERATIONS.md](OPERATIONS.md).

Para una instalación anterior, volver a ejecutar `--install-postfix-guard`: migra el guard conocido situado en las restricciones de remitente hacia las de destinatario, conservando bloqueos y respaldos. Una configuración ajena o modificada que no coincida con el respaldo se notifica para revisión.

### Ajustar límites y ritmo de salida

```bash
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600
```

La operación guarda los límites, crea/actualiza un bloque Ratelimits delimitado en `main.cf` y reinicia Postfix tras validarlo. Si faltan ajustes previos se solicita confirmación. Detalles, modos no interactivos y recomendaciones: [POSTFIX.md](POSTFIX.md).

## 5. Primera lectura

```bash
/root/scripts/outbound-spam.sh --install-service
```

Por defecto, `initial_position=end` comienza al final de `mail.log` y reconstruye las cuentas autenticadas que aún están en la cola. Monit indicará calentamiento hasta haber cubierto la ventana máxima: faltan los mensajes ya entregados antes de arrancar.

Desde 1.0.2 ese calentamiento se muestra con código correcto (0), fecha humana y zona local. No impide analizar/actuar sobre los datos disponibles ni oculta un incidente real.

Para comenzar por el principio del fichero actual al crear un estado nuevo:

```bash
# Antes de iniciar el servicio con un estado nuevo, configurar:
initial_position=start
```

Esto no reinicia un cursor existente. La lectura está acotada por `max_read_bytes`; si hay atraso se indicará como error operativo y se continuará en la siguiente ejecución. Los picos históricos ya fuera de la ventana no provocan cambios de contraseña ni otras acciones. Un exceso que siga dentro de la ventana actual sí puede abrir un incidente.

## 6. Servicio systemd y Monit

```bash
/root/scripts/outbound-spam.sh --install-service
/root/scripts/outbound-spam.sh --install-monit
systemctl status outbound-spam.service
```

La unidad ejecuta el propio script en modo `--daemon`. Espera **60 segundos después de terminar cada pasada**, sin solapamientos. Retirar las programaciones antiguas del servidor. `flock` coordina el servicio con las operaciones administrativas. Guía completa: [SERVICE.md](SERVICE.md).

El instalador de Monit utiliza estos directorios, que deben existir:

```text
/etc/monit/conf-available/
/etc/monit/conf-enabled/
/etc/monit/scripts/
```

Crea el servicio `Spam_Status`, valida `monit -t`, habilita el servicio de sistema y reinicia Monit. Si falla la validación, restaura los archivos que haya creado o actualizado. Los `include` de Monit deben cargar `conf-enabled/*`, como ocurre en la configuración de referencia.

El periodo real de refresco visual depende de `set daemon` de Monit; la referencia recibida usa 30 segundos. La prueba del programa es asíncrona y su resultado puede visualizarse en el siguiente ciclo.

## 7. Actualización y retirada

En una instalación gestionada por el release:

```bash
/root/scripts/outbound-spam.sh --upgrade
```

Consulta el último release estable público, verifica el paquete y preserva configuración/estado. El servicio se detiene y reanuda de forma coordinada con el bloqueo de operaciones. Procedimiento y límites de recuperación en [UPGRADE.md](UPGRADE.md).

Mantener el directorio de estado: contiene correlaciones, incidentes, intenciones de acciones y propiedad de retenciones. Para copiar SQLite con el analizador en marcha, utilizar su copia consistente o detener temporalmente el servicio y copiar el directorio completo, incluidos WAL/SHM si existen.

Desde 0.3.0 se conservan también los archivos de `recovery/` y la caché privada, situada desde 1.1.0 en `/var/lib/outbound-spam/outbound-spam.conf.last-good.json`. No restaurar el archivo SQLite aislado ignorando el diario crítico. Consultar [RECOVERY.md](RECOVERY.md).

Para retirar únicamente la integración SMTP:

```bash
/root/scripts/outbound-spam.sh --uninstall-postfix-guard
```

Primero deben haberse desbloqueado las cuentas. Se restauran los parámetros anteriores solo si todavía coinciden con los valores instalados, evitando sobrescribir cambios posteriores del administrador. El respaldo privado se conserva con un sufijo de retirada.

Para retirar servicio y monitor:

```bash
/root/scripts/outbound-spam.sh --uninstall-service
/root/scripts/outbound-spam.sh --uninstall-monit
```

Se conservan los datos, restricciones y mensajes retenidos.
