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

La instalación exige código de root en una ruta no modificable por otros y usa el nombre de unidad configurado; no sobrescribe archivos ajenos. La ejecución operativa requiere el servicio. Retirar las programaciones antiguas que pudieran quedar en el servidor.

## Funcionamiento

La unidad es `Type=simple`, con `Restart=on-failure` y `RestartSec=60`. El modo `--daemon` realiza este ciclo:

1. Lanza el mismo script con `--run` y espera su terminación.
2. Espera `run_interval`, por defecto **60 segundos desde el final** de la pasada.
3. Lanza la siguiente pasada.

Una pasada de 20 segundos produce un intervalo aproximado de 80 segundos entre inicios. No hay cola de trabajos ni ejecuciones simultáneas. Las alertas o errores del trabajador no terminan el bucle: se reflejan en el estado y se vuelve a intentar tras el descanso.

Hay dos bloqueos: `daemon.lock` impide dos demonios sobre el mismo estado y `lock` coordina cada pasada con los comandos administrativos. Los comandos manuales esperan hasta `lock_wait` segundos (120 por defecto); se puede indicar `--lock-wait 600`.

## Estado HTTP y NodePing

El demonio escucha mientras está activo, incluso durante una pasada, en la dirección IPv4 y el puerto configurados:

```bash
status_http_host=127.0.0.1
status_http_port=23456
curl -fsS http://127.0.0.1:23456/
```

El `GET /` devuelve HTTP 200 y el JSON completo de estado, incluidos cuentas y detalles. El servidor no cifra ni autentica las peticiones; por defecto solo escucha localmente. Para consultas externas usar un proxy inverso HTTPS y proteger el acceso. Cambiar dirección o puerto requiere `systemctl restart outbound-spam.service`; si no se puede abrir el puerto, el servicio falla al arrancar. Otros paths devuelven 404.

El campo `code` se recalcula en cada petición: 0 sano (incluido calentamiento), 1 incidente/retención/bloqueo y 2 error o estado ausente, inválido o atrasado. Se añade `http_metrics` con campos numéricos para NodePing: `code`, `last_success_age_seconds` (-1 si no se puede leer), `warming_up` (0/1), `incidents`, `held`, `blocked` e `issues`. El contenido detallado original permanece en el JSON. Cada campo numérico existente, incluidos campos de objetos y arrays mediante rutas como `incidents.0.peak`, puede evaluarse con HTTP Parse; los textos y booleanos no son valores numéricos para sus reglas de rango y los arrays vacíos no tienen elementos indexables.

En NodePing crear un check **HTTP Parse**, indicar la URL del proxy y añadir `http_metrics.code` con mínimo 0 y máximo 0. Se pueden añadir otros campos y rangos; si falta alguno, el check falla. NodePing también falla si no puede conectar. El check HTTP Parse no valida el certificado TLS: añadir un check SSL si se quiere supervisarlo.

Ejemplo dentro del `server` HTTPS de Nginx:

```nginx
location = /spam-status {
    proxy_pass http://127.0.0.1:23456/;
    proxy_set_header Host $host;
    proxy_buffering off;
}
```

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

Una configuración válida se conserva en `/var/lib/outbound-spam/outbound-spam.conf.last-good.json`, propiedad de root y modo 0600. Si durante un guardado aparece un archivo vacío, cambiante, incompleto o inválido, se utiliza la última configuración validada y se informa del problema en Monit y en el log. Nunca se activan silenciosamente los valores por defecto por leer un archivo temporalmente vacío.

Desde 1.1.0, el servicio y sus trabajadores escriben en `/var/log/outbound-spam.log` con tipo `servicio`: inicio/parada, estado por pasada y acciones ante incidencias. Los comandos administrativos usan tipo `manual`. El instalador configura la rotación semanal; véase [LOGGING.md](LOGGING.md).

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
