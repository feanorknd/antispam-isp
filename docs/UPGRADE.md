# Instalación del release y actualización

Repositorio público: **https://github.com/feanorknd/antispam-isp**.

## Instalación nueva desde el release 1.0.1

En un servidor Debian/Ubuntu con Postfix/ISPConfig configurados:

```bash
curl -fLO https://github.com/feanorknd/antispam-isp/releases/download/v1.0.1/antispam-isp-1.0.1.tar.gz
curl -fLO https://github.com/feanorknd/antispam-isp/releases/download/v1.0.1/SHA256SUMS
sha256sum -c SHA256SUMS
tar -xzf antispam-isp-1.0.1.tar.gz
sudo ./antispam-isp-1.0.1/install.sh --install-deps
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

La configuración operativa se conserva byte por byte, sin cambiar su inode, fecha de modificación ni permisos. Tampoco se añaden automáticamente variables. Si el ejemplo nuevo incorpora nombres ausentes tanto en el ejemplo anterior como en la configuración privada, se notifican en `missing_new_variables` para revisión; esta detección de asignaciones Bash es orientativa y no ejecuta el archivo privado.

Una actualización no regenera una configuración desaparecida: se cancela antes de cambiar el programa. En una instalación nueva solo se crea la configuración si no existía. También se conservan el estado, reservas de avisos, retenciones, bloqueos, configuración de Postfix y decisiones de relay. Se actualiza el ejemplo y, si existe, el comprobador Monit gestionado.

## Distribución instalada

```text
/root/scripts/outbound-spam.sh
/root/scripts/outbound-spam.conf
/root/scripts/outbound-spam-status.sh
/root/scripts/outbound-spam.conf.example
/root/scripts/.antispam/current -> releases/1.0.1
/root/scripts/.antispam/releases/1.0.1/
/root/scripts/.antispam/installation.json
```

Los módulos y documentación están dentro de la versión instalada. El lanzador fija `OUTBOUND_SPAM_BASE` para la configuración y `OUTBOUND_SPAM_CODE` para usar un conjunto coherente de módulos. Se conservan las versiones anteriores y las copias de actualización.

Una instalación plana anterior se convierte ejecutando `install.sh` del paquete nuevo. Se preserva el directorio `lib/` antiguo para no ocultar utilidades ajenas; el lanzador pasa a utilizar la distribución versionada. `--upgrade` requiere haber realizado esa instalación con gestor de releases.

## Fallos y recuperación de una actualización

Un fallo de descarga, checksum, validación o preparación conserva la versión anterior. Si todavía no se inició el nuevo servicio, se restauran el estado anterior y el punto de entrada y se intenta reactivar la unidad anterior.

Después de intentar iniciar el nuevo servicio no se rebobina automáticamente SQLite: el nuevo proceso podría haber enviado avisos o aplicado acciones. Si falla ese arranque se mantiene la versión preparada y su estado, se devuelve un error y se indica revisar `systemctl`/`journalctl`. Las copias privadas permiten una recuperación administrativa controlada.

Una operación manual que estuviera esperando durante el cambio de versión debe volver a ejecutarse: no se deja que código antiguo abra el estado después de activar un release nuevo.

El actualizador no sustituye la revisión de los cambios de versión. Las futuras distribuciones deben mantener el contrato `upgrade_api=1` y el modo de preparación; los paquetes incompatibles o sin los componentes requeridos se rechazan.
