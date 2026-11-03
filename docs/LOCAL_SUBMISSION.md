# Protección de webs PHP y sendmail — 1.3.0

## Identidad y dominio propio automáticos

El monitor lee `postfix/pickup: ID: uid=N from=<...>` y crea una identidad `local:N`. El PID de PHP-FPM o de pickup no forma parte de la cuota. El UID se resuelve con la base de usuarios UNIX; se conservan UID, login y directorio home.

Se consulta `web_domain` en la base ISPConfig configurada, filtrando por `ispconfig_server_id`. Las filas con el `system_user` correspondiente identifican las webs y pools propios. Se recorren sus relaciones `parent_domain_id` para incorporar alias y subdominios que compartan usuario o lo hereden. Un subdominio con usuario/pool diferente tiene su propia identidad. Se respetan también los valores de auto-subdominio `www` y `*` de ISPConfig: no se autoriza cualquier sufijo implícitamente. Se incluyen registros desactivados: sus procesos residuales tampoco deben escapar del control de volumen.

No hay listas manuales de dominios por `webX`, no se supone que `web6` sea el registro 6 y no se comparan IPs ni MX. Se consulta una instantánea cacheada por pasada cuando hay nuevas identidades que resolver. Cada mensaje conserva la asociación utilizada para calcular su cuota. Un dominio de la web puede tener el correo alojado fuera del servidor.

`foreign_sender` compara el dominio del remitente **del sobre** con los dominios asociados a esa web. La cabecera visible `From:` no sirve como prueba de identidad. Un dominio de otro cliente del mismo servidor sigue siendo foreign. Un remitente vacío no inventa un dominio. Esta comparación detecta diferencias de dominio, no acredita todas las autorizaciones comerciales de una aplicación.

Los usuarios sin web asociada se excluyen del seguimiento de webs; root y el usuario interno postfix se excluyen también. Un fallo SQL o un UID no resoluble se registra como cobertura incompleta, no como dominio propio confirmado. La consulta requiere el cliente `mysql`, acceso root por socket y la base/servidor ISPConfig correctos, igual que las integraciones SQL existentes.

## Cuotas y acciones

- Se reutilizan `max_sent`, `time_period`, `custom_limits`, `ignore` y `foreign_sender_divisor`.
- Solo cuentan destinatarios externos aceptados, incluidos pendientes; reintentos y reactivaciones no duplican unidades.
- La clasificación de destino sigue dependiendo de Postfix; el dominio remitente no decide si el destinatario es externo.
- Si hay externos con remitente foreign en la ventana, el umbral de esa identidad se divide por `foreign_sender_divisor`, con mínimo uno.
- Las identidades `local:UID` y las cuentas SASL tienen cuotas separadas, aunque coincidan sus remitentes.
- `block_local=true` publica la suspensión local. Es independiente de `block_smtp`.
- `hold_queue=true` permite retener los mensajes pendientes exclusivamente externos de la identidad. Los mixtos esperan a que terminen los destinatarios locales.
- `notify` controla sus avisos; se mantiene el mínimo de seis horas por identidad.
- `reset_password` nunca se aplica a identidades locales.

Ejemplo de reglas particulares, sin asignar dominios manualmente:

```bash
/root/scripts/outbound-spam.sh --set-custom-limit local:5009 300 3600
/root/scripts/outbound-spam.sh --set-ignore local:5010
```

## Mapa local y efecto del bloqueo

La instalación normal, con política previa `static:anyone`, queda así:

```ini
authorized_submit_users = !hash:/etc/postfix/outbound-spam-submit-blocked, static:anyone
```

Se antepone la exclusión gestionada a la política previa, incluso si esta era una lista vacía de denegación total. No se sustituye por un permiso general una política personalizada.

El mapa contiene claves de login UNIX, por ejemplo `web6 blocked`. El valor es indiferente: este parámetro solo consulta pertenencia; la negación está en `main.cf`. La fuente y el índice son root/0644, porque los consultan los usuarios que ejecutan sendmail/postdrop. Solo contienen nombres de usuarios, sin contraseñas ni evidencias privadas.

Cada publicación construye y verifica un índice temporal con `postmap`, sincroniza y sustituye atómicamente el `.db`. Los bloqueos/desbloqueos **no editan main.cf ni ejecutan reload**. La instalación o retirada del parámetro sí valida y recarga la instancia Postfix.

El disparador es el exceso de **externos**. Una vez suspendido, el usuario no puede inyectar ningún mensaje nuevo mediante sendmail/postdrop, tampoco destinado a buzones locales. El control SMTP AUTH continúa bloqueando únicamente destinatarios remotos. Los mensajes ya aceptados o que ya hayan superado la comprobación no quedan cancelados por la ACL.

La detección sigue siendo periódica, no una cuota atómica durante la aceptación. Esta ACL no controla conexiones SMTP sin AUTH a `localhost:25`, ni conexiones SMTP directas a otros servidores. Una entrada SMTP sin AUTH por loopback no ofrece el UID de la web en el log SMTP estándar.

## Instalación, upgrade y recuperación

`--upgrade` prepara automáticamente el parámetro y el mapa, ejecuta `postfix check` y `postfix -c RUTA reload`. Es idempotente. También instala esta integración `--install-postfix-guard` cuando `monitor_local=true`. Para administrarla por separado:

```bash
/root/scripts/outbound-spam.sh --install-local-guard
/root/scripts/outbound-spam.sh --uninstall-local-guard
```

La retirada requiere que no queden webs bloqueadas y restaura la política guardada en `STATE_DIR/postfix-submit-backup.json`. Una política modificada por terceros se señala para revisión, sin sobrescribirla. `monitor_local=false` desactiva nueva atribución local; `block_local=false` permite supervisar sin nuevos bloqueos automáticos. Ninguna de esas opciones retira los bloqueos ya publicados.

El esquema privado pasa a 4 conservando incidentes, reservas, bloqueos y HOLD. La identidad UNIX y las vinculaciones de cola necesarias para operaciones se incluyen en el diario crítico. Antes de bloquear se comprueba que UID/login/home no hayan cambiado. Para actuar sobre cola se exige un `pickup` fiable y se contrasta la generación del sobre por fecha, tamaño, remitente y `create_time`; nunca se extrae el UID de una cabecera `Received:` aportada por un cliente. No se atribuyen sobres sin SASL en el bootstrap por simple coincidencia de remitente.

Si falta o se daña el índice, se reconstruye a partir del estado duradero y se contrasta con la fuente gestionada. Bloqueos publicados sin historial privado suspenden los automatismos y se conservan para revisión. Las órdenes `--unblock`, `--resolve-user`, `--list-held`, `--release-held`, `--delete-held` y `--purge-user-queue` aceptan `local:UID` y conservan sus verificaciones de propiedad.

La cobertura local comienza con los eventos pickup leídos por el nuevo código. El primer upgrade marca calentamiento durante la ventana máxima; no reconstruye la identidad de correo anterior cuyo pickup ya quedó detrás del cursor. No borrar el estado para actualizar.

## Comprobaciones en un ISPConfig real

### Consultas sin envío ni bloqueo

Después del upgrade, como root y usando el usuario real de la web:

```bash
/root/scripts/outbound-spam.sh --version
postconf authorized_submit_users
postfix check
/root/scripts/outbound-spam.sh --doctor

getent passwd web6
ps -u web6 -o uid,pid,ppid,args
/root/scripts/outbound-spam.sh --inspect-local-user web6
/root/scripts/outbound-spam.sh --inspect-local-user 5009
/root/scripts/outbound-spam.sh --inspect-local-user web6 --sender recambios@gremiline.es
/root/scripts/outbound-spam.sh --inspect-local-user web6 --sender prueba@dominio-ajeno.invalid

postmap -s hash:/etc/postfix/outbound-spam-submit-blocked
systemctl is-active outbound-spam.service
/root/scripts/outbound-spam.sh --status
```

Esperado: versión 1.3.0 o posterior; la exclusión hash precede a la política antigua; UID, usuario y home coinciden; el dominio de la web y sus alias figuran en `domains`; los dos inspectores de identidad coinciden. Para el remitente propio, `foreign=false`; para el ajeno, `foreign=true`. `domains=null` significa que el usuario no se ha asociado a una web en el servidor configurado; revisar `ispconfig_database` y `ispconfig_server_id`. Estas consultas no requieren que siga vivo ningún PID PHP concreto. Un mapa vacío es normal si no hay webs bloqueadas.

También se puede revisar directamente el inventario SQL, adaptando la base y el servidor configurados:

```bash
mysql --protocol=socket --user=root dbispconfig --execute="SELECT domain_id,parent_domain_id,domain,system_user,type,subdomain,active FROM web_domain WHERE server_id=1 ORDER BY parent_domain_id,domain_id;"
```

### Prueba funcional opcional en una web de pruebas

Elegir una web dedicada, comprobar primero su UID y un remitente con `foreign=false`, y utilizar una dirección externa bajo vuestro control. Guardar sus límites previos. El ejemplo usa UID 5009 y login web6: sustituirlos por los de esa web de pruebas.

```bash
/root/scripts/outbound-spam.sh --set-custom-limit local:5009 2 300
```

Esperar una pasada para que el trabajador adopte el límite. Enviar tres mensajes pequeños mediante el mismo binario usado por PHP:

```bash
for n in 1 2 3; do
  printf 'Subject: Prueba outbound-spam 1.3.0 %s\n\nPrueba controlada.\n' "$n" |
    runuser -u web6 -- /usr/sbin/sendmail -i -f remitente@dominio-de-la-web.example -- buzon-externo-controlado@example.net
done
```

Después de la pasada del servicio, comprobar `--status` y `postmap -q web6 hash:/etc/postfix/outbound-spam-submit-blocked`: deben mostrar el incidente local y una clave publicada. Un envío posterior del mismo usuario será denegado por sendmail/postdrop incluso hacia un buzón local. No hay que reiniciar PHP ni Postfix al publicarse ese bloqueo.

Restablecer el límite previsto para la web y resolver la prueba con `--resolve-user local:5009`. Eso reconoce el incidente, libera las retenciones propias y retira la suspensión. `--ack` por sí solo no desbloquea. Consultar [OPERATIONS.md](OPERATIONS.md) antes de elegir borrado o purga.
