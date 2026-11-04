# Diseño técnico

## Flujo

```text
mail.log incremental ──→ cuenta SASL o pickup UID + generación de ID de cola
                                    │
                  líneas de entrega + sobre pendiente
                                    │
                         destinatarios externos
                                    │
                      ventanas deslizantes por cuenta
                                    │
                        incidente e intenciones SQLite
                                    │
              bloqueo SMTP / HOLD / contraseña / informe
                                    │
                           resumen atómico → Monit
```

## Identidad y unidad de cómputo

Para SMTP, una línea `ID: client=..., sasl_username=...` con un ID válido crea la atribución. Los errores de autenticación se ignoran. El bootstrap SMTP utiliza el atributo SASL del sobre, no una cabecera aportada por el cliente. Desde 1.3.0, `pickup ID: uid=N from=<...>` crea una identidad `local:N`, resuelta a usuario/home UNIX y dominios web de ISPConfig. El espacio `local:UID` está reservado: no se acepta como identidad SASL.

La comparación foreign local utiliza los dominios de la web, alias y subdominios asociados en `web_domain`, ampliados con su dominio registrable según la Public Suffix List incluida (ICANN y PRIVATE). La clasificación de los destinatarios sigue utilizando las rutas de Postfix. Detalles de las asociaciones y exclusiones en [LOCAL_SUBMISSION.md](LOCAL_SUBMISSION.md).

La primera línea `qmgr` con `nrcpt` confirma la aceptación y fija el instante de la ventana. Para un mensaje HOLD, que no pasa por `qmgr`, se puede completar la aceptación desde el fichero de cola finalizado. Los rechazos de cleanup/milter cierran transacciones no aceptadas.

La versión 0.1.1 sigue también las sesiones SMTP por servidor, servicio, PID y generación de conexión. Un resumen de desconexión sin DATA/BDAT aceptado cierra los IDs abandonados. Si hubo DATA/BDAT correcto, se permite que `qmgr` llegue después. Una transacción aún no aceptada no consume cuota ni genera por sí sola un error de cobertura; sí lo genera un mensaje ya aceptado cuyos destinatarios no se pueden determinar.

Cada combinación de generación de mensaje y destinatario efectivo se cuenta una vez. Un reintento o una reactivación de `qmgr` no genera un envío nuevo. Tras `removed`, reutilizar el mismo ID crea otra generación. La identidad del remitente puede ser distinta de la cuenta SASL.

Desde 0.2.0 se guarda el remitente del sobre y la diferencia de dominio con SASL. Si hay destinatarios externos de dominio remitente distinto en la ventana, el límite de toda la cuenta se divide entre cuatro. La evaluación conserva el exceso relativo más grave y su umbral efectivo, para no perder una alerta de 26/25 al compararla con una ventana ordinaria que permita 100.

`nrcpt` se utiliza para comprobar integridad, **no para asumir que todos los destinatarios son externos**. Las copias/alias locales no consumen el límite; una expansión a tres externos sí consume tres unidades.

## Destinatarios pendientes

El analizador conserva las líneas de entrega de cada mensaje y completa destinatarios todavía pendientes con `postcat -eq ID`. No necesita leer cuerpos para ese fin. La consulta al spool se hace para mensajes autenticados incompletos.

La clasificación observada usa el transporte. La clasificación pendiente consulta en modo automático:

- `transport_maps`, incluidas claves de destinatario, dominio, subdominio y comodín;
- `mydestination`;
- `virtual_mailbox_domains`;
- `relay_domains`;
- transportes por clase y `sender_dependent_default_transport_maps` cuando se conoce el remitente del sobre.

Se consultan los mapas de Postfix, no se copian sus contraseñas a la base del monitor. Se utiliza caché por ejecución para limitar procesos y consultas repetidos.

El alcance inicial es una instancia Postfix con entrega local LMTP y salida SMTP/relay, como la referencia suministrada. Las listas de dominios complejas no soportadas se señalan como error, y los transportes propios requieren clasificación explícita. Un filtro posterior a cola con reinyección, una redirección dinámica o un milter que cambie destinatarios de forma especial requieren validar su trazabilidad antes de considerar exacto el resultado. Los reenvíos que crean mensajes nuevos sin identidad SASL no se atribuyen por simple coincidencia de remitente.

## Ventanas y antigüedad

Los eventos se ordenan por su fecha, no por el minuto del cron. Para cada cuenta se usa una cola deslizante de eventos ponderados por destinatarios. Esto permite detectar picos entre ejecuciones y ventanas que cruzan un cambio de hora o de día.

Las fechas ISO conservan su offset de zona horaria. Para syslog clásico se infiere el año más cercano al reloj de referencia. En replay de fechas antiguas se debe proporcionar `--now`.

Los excesos que siguen dentro de la ventana actual se consideran actuales aunque el último envío tenga más de `max_action_age`. Ese parámetro limita únicamente la actuación sobre picos cuya ventana ya ha pasado. Los incidentes se mantienen hasta `--ack`; sus bloqueos y retenciones tienen su propia resolución.

## Lectura incremental y rotación

El cursor contiene dispositivo, inode, offset binario, prefijo y ancla de los últimos bytes consumidos. Solo se confirma después de una línea completa.

La pasada toma un límite de tamaño al abrir cada fichero sin comprimir. Si el log sigue creciendo, las líneas posteriores quedan para la siguiente pasada: no se persigue indefinidamente un archivo activo. En ejecución real se utiliza el reloj al evaluar y se registra por separado la fecha del análisis y la terminación correcta de la pasada.

- Renombrado: completar el inode antiguo y continuar con el actual.
- Compresión: recuperar el segmento conservado que coincida con las anclas.
- Copytruncate: detectar cambios de contenido aunque el fichero haya vuelto a crecer más allá del offset previo.
- Escrituras tardías: seguir el inode retirado durante `rotation_grace`.
- Rotados comprimidos e inmutables: no volver a descomprimirlos cada minuto después de completarlos.
- Hueco irrecuperable: registrar la pérdida y marcar calentamiento durante la ventana máxima.

`copytruncate` tiene una carrera propia entre copiar y truncar que ningún lector periódico puede eliminar. Se recomienda la rotación por renombrado y reapertura de rsyslog. El programa necesita que los rotados sigan disponibles hasta consumirlos.

La búsqueda de rotados está limitada al patrón `log_file*`. El presupuesto de bytes limita la ingestión ordinaria, pero recuperar una copia comprimida puede requerir descomprimir hasta el punto guardado.

## Persistencia

La base privada usa SQLite WAL, claves foráneas, transacciones y `synchronous=FULL`.

| Tabla | Datos |
|---|---|
| `meta` | Cursores, calentamiento, modo de ejecución, supresiones y metadatos |
| `messages` | Generaciones, identidad SASL/local, aceptación, asociación web, vinculación de sobre y muestras |
| `recipients` | Destinatarios efectivos, clasificación y último estado observado |
| `incidents` | Umbrales, picos, evidencias y reconocimiento |
| `actions` | Intenciones duraderas, intentos, resultado y payload privado |
| `held` | Propiedad y huella de mensajes retenidos |
| `blocks` | Cuentas cuya suspensión se ha solicitado |
| `acknowledgements` | Corte de evidencias ya revisadas |
| `smtp_sessions`, `smtp_pending` | Identidad de conexión y transacciones autenticadas aún sin confirmar |
| `notice_state` | Último intento y éxito por cuenta, independientes del ciclo de vida del incidente |
| `queue_ops` | Intenciones duraderas de purga, liberación, reencolado y resultado |

Eventos y cursor se confirman juntos. Un fallo antes del commit permite releer sin duplicar los eventos confirmados.

Las acciones externas se ejecutan después del commit y registran sus resultados individualmente. La operación sobre MySQL utiliza un hash objetivo guardado antes de modificar la cuenta y comparación con el hash anterior. Cada intento de aviso se reserva antes del envío: incluso ante un fallo incierto se esperan al menos seis horas para esa cuenta. Los avisos de otras cuentas tienen su propia reserva.

El resumen `status.json` se sustituye atómicamente. Monit no bloquea ni abre la base de datos.

Desde 0.3.0, las transacciones críticas se registran además en un diario independiente con checksums y manifiestos duplicados. Se renuevan instantáneas al abrir la operación, se conservan copias SQLite verificadas y se contrastan cursores y estado crítico antes de actuar. La recuperación nunca toma el remitente como prueba de propiedad de un HOLD. Detalles y casos irreparables: [RECOVERY.md](RECOVERY.md).

## Integración SMTP

El mapa SQLite de integración es independiente del estado privado y solo es legible por root y el grupo Postfix. `check_sasl_access` se coloca al principio de `smtpd_recipient_restrictions`. Para una cuenta bloqueada devuelve la clase `outbound_spam_remote`, que consulta la ruta del destinatario: `DUNNO` para locales, continuando las restricciones originales, o 451 para remotos. No devuelve `OK` para saltarse controles locales.

Las rutas se obtienen de los dominios/buzones/reenvíos/transportes de ISPConfig y se contrastan con los mapas reales de Postfix. Se resuelven alias virtuales conocidos; si alguno produce entrega externa, se considera remoto. La instantánea se actualiza transaccionalmente, conservando el inode, al bloquear y periódicamente. El cliente SMTP y proxymap permanecen sin permisos de escritura.

HOLD es una acción sobre el mensaje completo. El monitor espera a que terminen sus destinatarios locales y retiene únicamente cuando el sobre pendiente ya es exclusivamente externo. Esa espera puede permitir que una entrega externa se complete antes de la retención; el plan A no ofrece retención atómica por destinatario.

No se aplica una cuota en tiempo real. La secuencia es detección periódica → publicación del bloqueo → retención de cola configurada. Los mensajes aceptados o entregados antes de esa publicación siguen sujetos a la carrera propia del plan A.

Las webs usan una ACL distinta: un mapa hash negado en `authorized_submit_users`, que comprueban sendmail/postdrop en cada nueva ejecución. Se reconstruye fuera de servicio y se publica por sustitución atómica sin reload por incidente. Una vez activado por exceso externo, el bloqueo local impide inyecciones a cualquier destinatario. La instalación inicial/upgrade sí valida y recarga Postfix. Los metadatos críticos conservan la identidad UNIX y la generación de los sobres locales sobre los que se autorizan operaciones.

El perfil combina `default_destination_rate_delay=10s` y sus valores por transporte externo con `smtp_transport_rate_delay` (4s en el perfil 100/h). Local, LMTP y filtros locales reconocidos quedan a cero. El límite por cuenta del analizador suma destinatarios de todos sus transportes externos; los frenos nativos tienen bolsas por destino/transporte y cuentan entregas, no destinatarios. Las decisiones de relay global, por remitente o por `transport_maps` siguen perteneciendo a Postfix/ISPConfig. Detalles en [POSTFIX.md](POSTFIX.md).
