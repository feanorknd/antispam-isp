# Configuración

El archivo operativo es Bash, por defecto `/root/scripts/outbound-spam.conf`. Puede elegirse otro con `--config /ruta/privada.conf`. El ejemplo contiene todas las variables y se carga antes del archivo operativo.

## Umbrales y excepciones

```bash
max_sent=100
time_period=3600
report=(admin@example.org otro-admin@example.org)
ignore=(envios-controlados@example.org)
declare -A custom_limits=(
    ["boletin@example.org"]="3000:3600"
    ["aplicacion@example.org"]="200:300"
)
```

- La alerta se abre al superar el máximo, no al igualarlo.
- La ventana es `(instante - time_period, instante]`.
- Se cuenta cada destinatario externo de cada mensaje aceptado.
- Una autenticación puede producir muchos mensajes y cada mensaje muchos destinatarios.
- La misma dirección en mensajes distintos vuelve a contar; los intentos de entrega del mismo destinatario y mensaje no.
- Las cuentas SASL se normalizan a minúsculas, como en el uso habitual de ISPConfig. La parte local de las direcciones destinatarias se conserva.
- `ignore` excluye completamente la cuenta de la detección nueva y de la creación de acciones. No borra un incidente ni un bloqueo ya existente: utilizar los comandos administrativos correspondientes.
- Un límite particular sustituye tanto máximo como periodo de esa cuenta.

### Administrar límites y excepciones desde el script

```bash
sudo /root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 2000 900
sudo /root/scripts/outbound-spam.sh --set-ignore boletin@ejemplo.com
```

`--set-custom-limit CUENTA MAXIMO SEGUNDOS` guarda un máximo de **destinatarios externos** por ventana, sustituye el límite particular anterior y retira la cuenta de `ignore` si estaba ignorada. En el ejemplo, 2.000 destinatarios externos en 900 segundos; la alerta se abre al superar 2.000. Mantiene la reducción por dominio remitente distinto descrita abajo.

`--set-ignore CUENTA` añade la cuenta a ignorados y elimina su límite particular. Repetir cualquiera de las dos órdenes con los mismos valores no duplica reglas ni vuelve a escribir la configuración si no hay cambios. Para volver a supervisar una cuenta ignorada, asignarle el límite deseado:

```bash
sudo /root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 500 3600
```

Las cuentas se normalizan a minúsculas. Se comprueban el formato de la dirección, todos los argumentos, enteros positivos y que la retención cubra la ventana solicitada. La validación de dirección comprueba el formato, no la existencia del buzón en ISPConfig. Los errores devuelven un código distinto de cero. Una configuración incompleta o inválida debe corregirse antes de editarla administrativamente, aunque exista una última copia válida.

Se utiliza `/root/scripts/outbound-spam.conf` por defecto; `--config /ruta/privada.conf` permite elegir otro archivo. Las operaciones requieren root, esperan el bloqueo operativo y releen el archivo al obtenerlo para conservar cambios de otra orden que haya terminado mientras esperaban. No requieren herramientas de Postfix ni abrir la base de incidentes.

El script conserva el contenido previo del archivo y añade/actualiza un único bloque final `OUTBOUND-SPAM ACCOUNT RULES`, que contiene los valores efectivos completos de `ignore` y `custom_limits`. **Ese bloque prevalece sobre las declaraciones anteriores**: si se edita manualmente después, modificar las listas de ese bloque o utilizar los comandos. Así se conservan comentarios, arrays multilínea y ajustes ajenos sin intentar reescribir código Bash arbitrario. Las demás cuentas conservan sus reglas efectivas.

Antes de guardar se evalúa y valida la configuración resultante, y se verifica que no cambie ningún otro ajuste. El guardado es atómico, con modo 0600, respaldo privado en `/var/lib/outbound-spam/account-rules-backup-*.json` y actualización de la última copia válida. Un fallo durante el guardado intenta restaurar configuración y caché anteriores. La solicitud, los valores anteriores/nuevos y el resultado se registran en el log operativo.

Los cambios se adoptan en la siguiente pasada, sin reiniciar servicio ni Postfix. **No cierran incidencias, retiran bloqueos ni liberan o eliminan correo ya retenido**. Si se ha corregido un falso positivo, utilizar después `--resolve-user CUENTA` para resolverlo coordinadamente, o los comandos individuales de [OPERATIONS.md](OPERATIONS.md).

### Dominio remitente distinto

Se compara el dominio del **remitente SMTP del sobre** (`MAIL FROM`, registrado en `qmgr` o leído de la cola) con el de la cuenta SASL. No se utiliza la cabecera visible `From:`.

Si en la ventana hay destinatarios externos de un mensaje cuyo dominio remitente difiere, el límite de **toda esa cuenta en esa ventana** pasa a `max(1, límite_base // 4)`. Ejemplo: 100 → 25; una cuenta particular con límite 200 → 50. El disparo sigue siendo por superación estricta: con límite 25, el destinatario 26 provoca la alerta.

Los mensajes exclusivamente locales no activan esa reducción. Al salir de la ventana los envíos de dominio distinto, el contador actual recupera su límite base. El incidente ya abierto permanece hasta revisión. Los dominios se comparan sin distinguir mayúsculas y normalizando IDNA; un remitente vacío `<>` no se inventa como otro dominio.

`foreign_sender_divisor=4` controla el divisor. Un remitente diferente dentro del mismo dominio no reduce el límite. La relación entre los dominios no demuestra por sí sola una suplantación: los envíos delegados autorizados deben tener un perfil adecuado.

## Acciones

| Variable | Valor de ejemplo | Efecto |
|---|---|---|
| `notify` | `true` | Envío inicial y recordatorios a `report` |
| `block_smtp` | `false` | Responder 451 a destinatarios remotos de la cuenta; mantener los controles originales para locales |
| `hold_queue` | `false` | Retener mensajes pendientes de esa cuenta cuando ya no quedan entregas locales pendientes |
| `reset_password` | `false` | Sustituir el hash de contraseña en ISPConfig y vaciar su caché de autenticación |
| `reminder_interval` | `21600` | Segundos entre intentos de aviso por cuenta; mínimo obligatorio de seis horas |
| `sample_count` | `10` | Número máximo de mensajes de ejemplo |

Al abrir un incidente se registran las acciones habilitadas. Desactivar una acción suspende sus reintentos pendientes; no revierte acciones ya completadas. Para incorporar bloqueo/cambio de contraseña a un incidente anterior que se abrió con esas opciones desactivadas, reconocerlo y dejar que la actividad nueva cree otro incidente, o resolverlo administrativamente.

Mientras el incidente siga abierto, `hold_queue=true` repite la búsqueda para capturar mensajes que hayan entrado entre ejecuciones. Una orden manual de liberar o eliminar su correo cancela esa repetición para el incidente revisado.

El informe de correo siempre corresponde a una sola cuenta. La separación mínima de seis horas se conserva entre reinicios, cambios de límites y reconocimiento/reapertura de incidentes. Un valor antiguo de 3600 se eleva automáticamente a 21600; se puede elegir un intervalo mayor.

## Edición con nano y servicio

Se puede editar y guardar el archivo mientras el servicio está activo. Cada pasada utiliza una instantánea validada. Si se lee un guardado incompleto o inválido, se mantiene la última configuración válida, en vez de aplicar valores por defecto o detener el bucle. El problema se indica en Monit. Las opciones administrativas que modifican la instalación requieren corregir primero esa configuración inválida.

La caché `/var/lib/outbound-spam/ARCHIVO.conf.last-good.json` contiene valores privados y tiene modo 0600; el nombre predeterminado es `outbound-spam.conf.last-good.json`. Los cambios válidos se adoptan al iniciar una nueva pasada. Guardar parámetros de Postfix con nano no edita por sí solo `main.cf`: usar `--configure-postfix` para aplicarlos. Las rutas de instancia/estado requieren una migración y reinstalación controladas; la unidad fija su directorio de estado. Véase [SERVICE.md](SERVICE.md).

## Lectura y estado

| Variable | Defecto | Uso |
|---|---:|---|
| `log_file` | `/var/log/mail.log` | Log único de la instancia Postfix |
| `state_dir` | `/var/lib/outbound-spam` | SQLite privada, resumen, informes y respaldos |
| `initial_position` | `end` | `start` o `end`, solo al crear el cursor |
| `max_read_bytes` | `67108864` | Presupuesto de bytes por ejecución, aproximadamente 64 MiB |
| `command_timeout` | `30` | Tiempo máximo de cada comando externo/conexión SMTP |
| `stale_after` | `180` | Segundos sin ejecución correcta antes de marcar error |
| `run_interval` | `60` | Descanso del servicio después de terminar una pasada |
| `status_http_host` | `127.0.0.1` | Dirección IPv4 del listener HTTP del servicio |
| `status_http_port` | `23456` | Puerto TCP del estado JSON en `/` |
| `lock_wait` | `120` | Espera máxima de administración; `--lock-wait` permite otra espera |
| `service_unit` | `/etc/systemd/system/outbound-spam.service` | Unidad de systemd gestionada |
| `backup_interval` | `300` | Intervalo mínimo entre copias SQLite consistentes |
| `backup_copies` | `3` | Copias SQLite verificadas conservadas |
| `integrity_interval` | `300` | Intervalo de comprobación del mapa Postfix; el estado privado se comprueba al abrir |
| `rotation_grace` | `300` | Seguimiento de escrituras tardías en un inode rotado |
| `retention_days` | `14` | Retención de mensajes terminados; debe cubrir las ventanas configuradas |
| `max_action_age` | `180` | Antigüedad máxima de picos que ya no siguen excediendo la ventana actual |

Las correlaciones de mensajes aún activos y las evidencias de los incidentes se conservan. Los informes e incidentes requieren una política de archivo del administrador; la versión inicial no los elimina automáticamente. No se ejecuta un `VACUUM` completo cada minuto.

`stale_after` tiene un mínimo efectivo de `run_interval + 120`. Una pasada excepcionalmente larga puede indicar análisis atrasado en Monit, aunque el servicio esté trabajando: no se crea otra en paralelo para compensarlo.

La dirección y puerto HTTP se aplican al reiniciar el servicio. El endpoint muestra el estado completo; proteger el acceso externamente. Véase [SERVICE.md](SERVICE.md#estado-http-y-nodeping).

## Clasificación externa

```bash
routing_mode=auto
external_transports=(smtp relay)
local_transports=(local virtual lmtp dovecot maildrop mailman discard error)
```

Las líneas de entrega de los transportes configurados dan la clasificación observada. Para destinatarios pendientes, el modo `auto` consulta los transportes, clases de dominio y mapas reales de Postfix. Los transportes SMTP con nombre propio deben añadirse a `external_transports`; los locales a `local_transports`.

Para una instalación sencilla o pruebas offline:

```bash
routing_mode=static
local_domains=(example.org interno.example.net)
```

El modo estático considera externo todo dominio que no esté en esa lista. No interpreta comodines ni reproduce excepciones de transporte. Los mapas y dominios se almacenan en caché durante una ejecución, no indefinidamente.

El bloqueo remoto publica una instantánea de rutas para Postfix. En modo automático, sin `local_domains` explícitos, enumera dominios, buzones, reenvíos y transportes de ISPConfig mediante MySQL local como root y contrasta sus rutas con los mapas de Postfix. Si se facilita una lista explícita de dominios se omite esa enumeración; `guard_route_addresses` permite añadir alias/rutas concretas que deban resolverse. La instantánea se refresca al bloquear y periódicamente mientras haya bloqueos.

Los alias virtuales conocidos que producen alguna entrega externa se consideran remotos a efectos de bloqueo, aunque su nombre pertenezca a un dominio alojado. Las reglas complejas ajenas a los mapas de ISPConfig y los reenvíos posteriores de Sieve/`.forward` requieren validación específica. Un filtro SMTP en loopback no se clasifica automáticamente como salida a Internet: se comprueba la ruta final de su destinatario.

Validar también las combinaciones de dominios alias con reenvíos específicos: las direcciones resultantes que no estén enumeradas directamente pueden añadirse a `guard_route_addresses` para publicar su ruta exacta. Los mapas dependientes del remitente que cambien el transporte final, en lugar de seleccionar otro relay SMTP, requieren comprobar sus excepciones de localidad.

## Informes

```bash
alert_transport=sendmail
alert_from=outbound-spam@example.org
```

`sendmail` utiliza la cola local. Para un canal directo independiente:

```bash
alert_transport=smtp
smtp_host=smtp-alertas.example.org
smtp_port=587
smtp_security=starttls
smtp_user=alertas@example.org
smtp_password='contraseña-del-canal-de-alertas'
```

`smtp_security` admite `plain`, `starttls` o `tls`. En TLS se valida el certificado usando las CA del sistema. Los informes quedan guardados además en `state_dir/reports/`, incluso con `notify=false` durante el funcionamiento operativo.

El intento se reserva en SQLite **antes** de llamar a SMTP/sendmail. Si falla o queda incierto, no se reintenta para esa cuenta antes de seis horas; Monit refleja el fallo. Se prioriza evitar avisos duplicados del proceso sobre el reintento inmediato. La reserva es por cuenta, no global, y no se borra al reconocer un incidente.

## ISPConfig

```bash
ispconfig_database=dbispconfig
ispconfig_server_id=1
mysql_socket=
```

El cliente MySQL usa conexión local por socket, usuario root, sin contraseña en argumentos. Se comprueba que hay exactamente una fila con ese email y servidor.

Se admite el hash `$6$` SHA512-CRYPT, con o sin prefijo `{SHA512-CRYPT}`. Otros formatos generan un error de la acción sin modificar la contraseña. Antes de escribir se comprueba la capacidad de la columna; la actualización usa comparación con el hash anterior y se verifica el resultado.

La contraseña aleatoria se genera solo para crear un hash; no se envía ni se conserva en claro. El administrador establecerá una contraseña nueva desde ISPConfig. Si se interrumpe la acción entre SQL y el vaciado de caché, el reintento reconoce el hash ya aplicado y no vuelve a cambiarlo.

Cambiar la contraseña afecta también a la autenticación para correo local/IMAP. Para mantener las credenciales durante la contención exclusivamente remota, conservar `reset_password=false` y utilizar el bloqueo remoto.

## Rutas de integración

El ejemplo permite configurar las rutas de todos los ejecutables y los archivos de Postfix y Monit. `guard_map` es el prefijo de `.cf`, `-recipient.cf` y `.sqlite3`.

Los archivos de integración pertenecen a `root:postfix`, modo `0640`, y contienen cuentas y rutas, sin contraseñas. Los informes, hashes temporales de acciones, evidencias y contadores permanecen en la base privada `0600`.

## Ajuste coordinado por parámetros

Consultar [POSTFIX.md](POSTFIX.md). Ejemplo:

```bash
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600
```

Guarda los valores en el archivo de configuración, instala/actualiza el bloque delimitado de `main.cf`, comprueba su validez y reinicia la instancia. Si no encuentra ajustes previos, pregunta antes de crear el bloque. `--yes` acepta expresamente esa creación; `--skip-postfix` modifica solo la configuración del analizador.

`postfix_destination_delay=10s` mantiene la protección por destino además de `postfix_outbound_delay=auto`. Puede fijarse por comando con `--destination-delay 10s`. Local, LMTP y las etapas locales reconocidas quedan exentos de ambos retrasos.
