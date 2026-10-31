# Operación

Los ejemplos asumen el script instalado en `/root/scripts/outbound-spam.sh`.

## Consulta y ejecución

```bash
/root/scripts/outbound-spam.sh --run
/root/scripts/outbound-spam.sh --status
/root/scripts/outbound-spam.sh --doctor
```

`--status` muestra el JSON de resumen y comprueba su antigüedad. Incluye cuentas, volumen de destinatarios externos, límite aplicable, incidentes, retenciones, bloqueos y métricas de la última lectura.

### Códigos

| Comando | `0` | `1` | `2` |
|---|---|---|---|
| `--run` | Lectura completada, con o sin incidente | — | Error operativo/cobertura incompleta |
| `--status` | Sano | Incidente, retención o bloqueo | Error o estado atrasado |
| Comprobador Monit | Sano | Incidente, retención o bloqueo | Error, estado ausente o atrasado |

El calentamiento por sí solo es informativo y devuelve 0. `warming_up` e `info` lo describen sin añadirlo a `issues`. Las fechas de comandos e informes se muestran en la zona horaria del servidor, con abreviatura y offset; por ejemplo `2026-10-30 21:03:07 CET (UTC+01:00)`. Las líneas originales de log de las evidencias se conservan literalmente. Los epochs del archivo de estado/SQLite siguen siendo internos y no se convierten para los cálculos.

`--help` incluye la explicación de cada operación y una guía de respuesta a incidentes con ejemplos sencillos. El mismo comando puede invocarse desde un enlace a su lanzador estable, sin depender del directorio actual.

Si otra ejecución mantiene el bloqueo, `--run` termina sin duplicar trabajo; Monit detectará un proceso permanentemente atascado por la antigüedad del resumen.

El servicio ejecuta pasadas secuenciales y espera 60 segundos desde el final. Los comandos administrativos esperan el bloqueo; `--lock-wait 600` permite ampliar esa espera. No necesitan parar el servicio para actuar sobre la cola.

## Revisar un incidente

1. Consultar `--status` y el informe `state_dir/reports/incident-N.txt`.
2. Revisar IPs, cuenta SASL, destinatarios externos, ventana y ejemplos.
3. Comprobar el resultado individual de bloqueo SMTP, retención, contraseña y notificación.
4. Resolver la credencial comprometida y decidir el destino del correo retenido.

El aviso de un exceso no demuestra por sí solo que la contraseña esté comprometida: también puede corresponder a una campaña legítima. Las excepciones y límites particulares permiten adaptar esa actividad.

## Correo retenido

```bash
/root/scripts/outbound-spam.sh --list-held cuenta@example.org
/root/scripts/outbound-spam.sh --release-held cuenta@example.org
/root/scripts/outbound-spam.sh --delete-held cuenta@example.org
```

La selección utiliza la identidad SASL del sobre y la huella de creación del mensaje. La dirección `From:` o el remitente del sobre no autorizan la selección. Las retenciones previas de otros administradores no se incorporan al inventario del monitor.

- `--release-held` utiliza `postsuper -H` para retenciones recientes.
- Si la edad supera la mitad de la menor vida máxima de cola configurada (`maximal_queue_lifetime` y `bounce_queue_lifetime`), utiliza `postsuper -r` para evitar que el mensaje caduque inmediatamente tras liberarlo.
- `--delete-held` utiliza `postsuper -d ID hold`, limitado a la cola HOLD.
- Nunca se utilizan operaciones `ALL` ni se vacía la cola completa.
- Liberar/eliminar cancela la repetición automática de HOLD para ese incidente, pero mantiene su bloqueo SMTP y su estado de revisión.

La retención es por mensaje completo. Para preservar la entrega local, el monitor **no aplica HOLD mientras queden destinatarios locales pendientes**: lo vuelve a considerar tras completar esos destinatarios. Esto también permite retener después los externos de un mensaje que ya entregó su copia local. No puede retirar entregas completadas ni interceptar con certeza una entrega en curso. Una retención previa de una versión antigua puede incluir locales; no se libera automáticamente.

Las comprobaciones reducen la confusión por reutilización de IDs; Postfix no ofrece una transacción atómica entre la inspección del sobre y `postsuper`. Los IDs largos reducen esa posibilidad. Su activación, si se desea, se administra de forma independiente con los procedimientos habituales de Postfix.

## Ajustar límites y excepciones por cuenta

```bash
# 2.000 destinatarios externos en 900 segundos; sustituye su límite y retira de ignore.
/root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 2000 900

# Ignorar la cuenta y retirar su límite particular.
/root/scripts/outbound-spam.sh --set-ignore boletin@ejemplo.com

# Volver a supervisarla, ahora con 500 destinatarios externos por hora.
/root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 500 3600
```

Se guardan en `outbound-spam.conf`, conservan las demás cuentas y se aplican en la siguiente pasada. Dirección, máximo y periodo se validan antes de guardar; una repetición idéntica no duplica reglas. El resultado muestra la regla anterior/nueva y se registra en el log. Los límites cuentan destinatarios externos, no mensajes, y conservan la regla de dominio remitente distinto.

Estas órdenes no resuelven automáticamente una incidencia ni revierten contenciones ya aplicadas. Los detalles de persistencia y edición manual están en [CONFIGURATION.md](CONFIGURATION.md).

## Desbloquear y reconocer

```bash
/root/scripts/outbound-spam.sh --unblock cuenta@example.org
/root/scripts/outbound-spam.sh --ack cuenta@example.org
```

Son operaciones independientes:

- **Desbloquear** permite enviar de nuevo al exterior, sin liberar mensajes ni cerrar el incidente.
- **Reconocer** cierra el incidente y fija un corte para no reabrirlo por las mismas evidencias antiguas. No desbloquea ni libera correo.
- Una actividad nueva posterior al reconocimiento puede abrir otro incidente.

Los avisos por correo conservan un intervalo mínimo de seis horas **por cuenta**, incluso entre incidentes distintos. El reconocimiento no borra esa reserva. La detección, las acciones de contención habilitadas y la actualización de Monit siguen ejecutándose cada minuto.

El monitor sigue en alerta si quedan retenciones o bloqueos registrados. Una operación administrativa no renueva la fecha de la última lectura del analizador.

## Vaciar la salida de una cuenta comprometida

```bash
/root/scripts/outbound-spam.sh --block-user cuenta@example.org
/root/scripts/outbound-spam.sh --purge-user-queue cuenta@example.org
```

El primer comando publica la suspensión externa inmediatamente (requiere guard instalado). El segundo inspecciona `active`, `deferred` y `hold`, verifica la cuenta SASL del sobre y la huella, retiene cada mensaje externo antes de borrarlo y limita el borrado a HOLD. No selecciona por el remitente falsificable.

La purga permite eliminar también mensajes del usuario que no habían sido retenidos por el monitor. Conserva mensajes locales y mixtos con locales pendientes, e informa de cuántos ha conservado. Una entrega ya completada no se puede retirar. La purga por sí sola no impide que entren nuevos mensajes: para eso está el bloqueo previo. El borrado es irreversible.

## Resolver un falso positivo de forma coordinada

```bash
/root/scripts/outbound-spam.sh --resolve-user cuenta@example.org
```

Cancela acciones pendientes del incidente, lo reconoce, libera/reencola sus retenciones propias y finalmente retira la suspensión externa. Conserva la reserva de seis horas de los avisos. Si hay un fallo, mantiene una resolución pendiente y no declara terminada una operación parcial; el servicio puede reconciliar sus intenciones duraderas.

Incluye un HOLD creado por una purga interrumpida antes del borrado: cancela ese borrado y libera el mensaje verificado. No se apropia de retenciones previas de otras herramientas. Si se pierde el historial crítico, exige recuperarlo antes de afirmar que ha resuelto todas las retenciones/restricciones.

Para evitar que la actividad legítima vuelva a superar el umbral, corregir antes su límite particular con `--set-custom-limit` o su excepción con `--set-ignore`. Una contraseña ya cambiada no se puede recuperar: debe establecerse otra desde ISPConfig.

## Errores habituales

| Situación | Tratamiento |
|---|---|
| `ventana en calentamiento` | Estado informativo, no error de Monit; esperar a cubrir la mayor ventana |
| `pendiente de leer log` | Continuará en el siguiente minuto; ajustar presupuesto si es necesario |
| `destinatarios/aceptación incompletos` | Falta información de un mensaje aceptado; comprobar la siguiente ejecución y la disponibilidad del spool |
| `Transporte sin clasificar` | Añadir el transporte a los arrays adecuados |
| `Mapa ... no soportado` | Revisar el mapa o utilizar clasificación estática explícita |
| Acción de contraseña fallida | Verificar formato de hash, fila ISPConfig, socket MySQL y `doveadm` |
| Acción SMTP fallida | Comprobar `postfix-sqlite`, `proxy_read_maps` e integración instalada |
| Aviso por sendmail atrasado | Revisar la cola o utilizar un canal SMTP directo para informes |
| Estado atrasado en Monit | Revisar cron, permisos, bloqueo de ejecución y errores del script |

### «Cola no reconocida» y subdirectorios Postfix

Una cabecera como `*** ENVELOPE RECORDS deferred/A/ABC123DEF0 ***` es normal: `deferred` es la cola y `A` un subdirectorio de hash. Desde 1.2.1 se reconocen rutas planas y con varios niveles para IDs cortos/largos, incluidas disposiciones distintas entre colas. Versiones anteriores podían mostrar `Cola no reconocida` para esos mensajes; actualizar y esperar la siguiente pasada, conservando el estado existente.

Si persiste después de actualizar, consultar `postcat -eq ID` para el ID afectado y revisar la primera línea. El código 2 indica cobertura incompleta y no equivale a una detección de spam. El calentamiento por sí solo no provoca ese error. Las colas desconocidas o sobres que no corresponden exactamente al mensaje solicitado siguen tratándose como errores.

Los rechazos `milter-reject`/`milter-discard` anteriores a aceptación no se contabilizan como envíos ni se mantienen como transacciones pendientes.

Tampoco se consideran envíos los IDs asignados a sesiones que terminan con RSET o DATA fallido sin aceptación. El seguimiento de esas desconexiones y la distinción entre DATA en curso y cobertura de mensajes aceptados se corrigieron en 0.1.1 tras la simulación histórica.

## Análisis offline

Con un archivo de configuración privado y un directorio de estado distinto del operativo:

```bash
bash outbound-spam.sh --config /ruta/privada/monitor.conf \
  --replay /ruta/mail.log \
  --state-dir /ruta/privada/replay \
  --local-domain example.org \
  --now 1793275200
```

`--now` es una fecha UNIX posterior a los eventos de la muestra. Por defecto se usa el reloj real. Replay no ejecuta comandos de Postfix, SQL de ISPConfig ni notificaciones. Usa clasificación estática para destinatarios cuyo transporte no esté registrado y puede señalar cobertura incompleta si la muestra termina con mensajes pendientes.

Si el archivo supera `max_read_bytes`, repetir la misma orden: continúa desde el cursor guardado. No mezclar estado offline con estado operativo; el motor lo comprueba.

## Programación recomendada

```bash
/root/scripts/outbound-spam.sh --install-service
systemctl status outbound-spam.service
```

La instalación/desinstalación y la edición con nano se describen en [SERVICE.md](SERVICE.md). La recuperación automática se describe en [RECOVERY.md](RECOVERY.md).

### Cron para instalaciones anteriores

La tarea de cron instalada es:

```cron
* * * * * root /root/scripts/outbound-spam.sh --config /root/scripts/outbound-spam.conf --run --quiet
```

Monit ejecuta su comprobador en sus propios ciclos y no analiza de nuevo `mail.log`. Los recordatorios son generados por el analizador según `reminder_interval`. Los comandos de instalación se ejecutan en cada servidor de destino.
