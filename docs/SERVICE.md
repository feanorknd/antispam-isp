# Servicio systemd y configuración en caliente

## Instalación

Tras copiar todos los componentes y configurar las direcciones reales:

```bash
/root/scripts/outbound-spam.sh --install-service
/root/scripts/outbound-spam.sh --install-monit
systemctl status outbound-spam.service
```

Se instala `/etc/systemd/system/outbound-spam.service`, se ejecuta `daemon-reload`, se habilita para el arranque y se inicia. El programa es el propio script:

```text
ExecStart=/bin/bash /root/scripts/outbound-spam.sh --config /root/scripts/outbound-spam.conf --state-dir /var/lib/outbound-spam --daemon
```

La instalación exige código de root en una ruta no modificable por otros. Utiliza el cron y el nombre de unidad establecidos en la configuración; no sobrescribe archivos ajenos.

El instalador retira `/etc/cron.d/outbound-spam` si pertenece a esta aplicación. Las entradas añadidas manualmente en otros crontabs se retiran manualmente. Un bloqueo sigue evitando ejecuciones simultáneas incluso si quedara una llamada externa.

## Funcionamiento

La unidad es `Type=simple`, con `Restart=on-failure` y `RestartSec=60`. El modo `--daemon` realiza este ciclo:

1. Lanza el mismo script con `--run` y espera su terminación.
2. Espera `run_interval`, por defecto **60 segundos desde el final** de la pasada.
3. Lanza la siguiente pasada.

Una pasada de 20 segundos produce un intervalo aproximado de 80 segundos entre inicios. No hay cola de trabajos ni ejecuciones simultáneas. Las alertas o errores del trabajador no terminan el bucle: se reflejan en el estado y se vuelve a intentar tras el descanso.

Hay dos bloqueos: `daemon.lock` impide dos demonios sobre el mismo estado y `lock` coordina cada pasada con los comandos administrativos. Los comandos manuales esperan hasta `lock_wait` segundos (120 por defecto); se puede indicar `--lock-wait 600`. Una ejecución puntual `--run` sin espera explícita omite el trabajo si hay otro en curso.

## Administración habitual

```bash
systemctl start outbound-spam.service
systemctl stop outbound-spam.service
systemctl restart outbound-spam.service
systemctl status outbound-spam.service
journalctl -u outbound-spam.service
```

SIGTERM impide una nueva pasada y permite terminar la actual. `KillMode=mixed` y `TimeoutStopSec=600` dan hasta diez minutos de cierre antes de que systemd termine los procesos restantes. El registro crítico permite reconciliar operaciones interrumpidas.

`service-status.json` es un diagnóstico local del proceso y su trabajador, no un gestor de procesos externo. La supervisión de arranque/parada corresponde a systemd; Monit consulta el resumen de spam y su antigüedad.

## Unidad y upgrade

El actualizador 1.0.2 actualiza el código y también la unidad propia intacta cuando la plantilla cambia, con copia previa y `daemon-reload`. Conserva la línea `ExecStart`, los drop-ins y si la unidad estaba parada: no habilita ni arranca servicios que no lo estuvieran. Un archivo base modificado manualmente se conserva y el resultado incluye `service_unit_note`. Las unidades no instaladas no se crean como efecto de un upgrade.

El paso 1.0.1 → 1.0.2 todavía lo despliega el actualizador antiguo. El código del servicio se renueva y su unidad sigue siendo válida porque esta versión no cambia su contenido operativo. Para regenerarla expresamente existe `--install-service`.

## Calentamiento y fechas

La ventana inicial se presenta como `CALENTAMIENTO`, con código 0 si no hay otra incidencia; Monit no alerta por ese motivo. Un ataque sigue devolviendo 1 y un error real 2, incluso durante el calentamiento. La salida muestra la hora local y su zona/offset. Las fechas numéricas del archivo de estado son internas para los cálculos del comprobador; `--status` las presenta en formato humano.

## Comando desde cualquier directorio

El lanzador resuelve enlaces simbólicos absolutos, relativos y encadenados antes de buscar código/configuración. Puede usarse un enlace como:

```bash
ln -s /root/scripts/outbound-spam.sh /usr/local/sbin/outbound-spam
outbound-spam --status
outbound-spam --help
```

Si el enlace ya existe apuntando al lanzador estable, basta con actualizar el programa. Para actualizar un lanzador antiguo que aún falla al resolver enlaces, usar la ruta absoluta `/root/scripts/outbound-spam.sh --upgrade`. No apuntar al código de una versión concreta dentro de `releases/`, sino al lanzador estable.

## Editar con nano

```bash
nano /root/scripts/outbound-spam.conf
```

Cada pasada obtiene una instantánea estable del archivo, comprueba sintaxis Bash y valida valores/permisos. No modifica su política a mitad de una operación. Los guardados en sitio y los que sustituyen el inode están contemplados.

Una configuración válida se conserva en `outbound-spam.conf.last-good.json`, propiedad de root y modo 0600. Si durante un guardado aparece un archivo vacío, cambiante, incompleto o inválido, se utiliza la última configuración validada y se informa del problema en Monit. Nunca se activan silenciosamente los valores por defecto por leer un archivo temporalmente vacío.

Los cambios válidos de límites, informes, excepciones y acciones se aplican a la siguiente pasada. `run_interval` se transmite al bucle al completar el trabajador. Guardar con nano **no reinicia Postfix**: para trasladar ajustes a `main.cf` se utiliza `--configure-postfix`.

La unidad fija la ruta de estado. Cambiar `state_dir` requiere parar el servicio, trasladar de forma consistente su estado y reinstalar la unidad con la ruta nueva. No se debe cambiar la identidad/ruta de una instancia activa como si fuera un simple umbral; se conserva el estado anterior y se indica que la unidad tiene una ruta fijada.

Si no hay ninguna configuración inicial válida ni caché verificable, el proceso no inicia acciones con parámetros supuestos. systemd reintentará el arranque y Monit mostrará estado ausente/atrasado.

## Desinstalación

```bash
/root/scripts/outbound-spam.sh --uninstall-service
/root/scripts/outbound-spam.sh --uninstall-monit
```

El primer comando detiene y deshabilita la unidad y retira su archivo. El segundo retira únicamente `Spam_Status` y su comprobador, valida Monit y lo reinicia conservando los demás monitores.

Ambas operaciones conservan SQLite, evidencias, bloqueos Postfix y mensajes retenidos. Para resolver una cuenta se usan los comandos de cola descritos en [OPERATIONS.md](OPERATIONS.md). Para retirar también la integración Postfix existe `--uninstall-postfix-guard`.

El modo cron se conserva por compatibilidad. `--install-cron` rechaza instalar una segunda programación si existe la unidad gestionada; `--uninstall-cron` retira solo el cron propio.
