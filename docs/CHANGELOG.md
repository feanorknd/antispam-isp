# Cambios efectuados

## 1.3.1 — 2026-11-03

- Contrato permanente de configuración completa: cada upgrade incorpora todas las variables ausentes del `.conf`, usando el `.conf.example` del candidato como inventario, incluidas variables antiguas y arrays.
- Bloque inicial de valores predeterminados que conserva el contenido previo y su prioridad; validación de equivalencia, respaldo privado, escritura atómica y actualización de caché.
- `--upgrade` sin versión nueva también completa la configuración y sincroniza el ejemplo, sin reiniciar servicios; operación idempotente.
- Rollback de la ampliación propia sin deshacer ediciones externas y cobertura de fallos de caché, concurrencia y futuras variables.
- Actualizadores públicos hasta 1.3.0 probados; se reproduce la ausencia de `monitor_local`/`block_local` y su incorporación automática con Postfix real.
- Pruebas públicas del contrato en `tools/test_config_completion.py`. Se mantiene esquema SQLite 4 y API de upgrade 1.

## 1.3.0 — 2026-11-03

- Atribución de correo local desde pickup por UID, independiente de los PID de PHP-FPM, con identidades `local:UID` y cuotas de destinatarios externos.
- Descubrimiento automático de dominios web, alias/subdominios y auto-subdominios ISPConfig para foreign; pools con usuarios distintos quedan separados.
- ACL hash negada en `authorized_submit_users`, instalada automáticamente durante upgrade con validación/reload. Publicación atómica de bloqueos/desbloqueos sin reload por incidente y preservando la política previa.
- Acciones locales diferenciadas, sin reset de contraseña; límites/excepciones, avisos, HOLD y resolución compatibles con identidades locales.
- Esquema 4 y recuperación duradera de identidad UNIX y vinculaciones de cola; detección de UID/IDs sustituidos y conservación de bloqueos sin historial.
- Inspector `--inspect-local-user UID_O_USUARIO [--sender REMITENTE]`, instaladores de ACL y batería de comprobaciones reales documentada.
- 213 pruebas completas y 9 HTTP correctas en Focal y Noble; 25 pruebas locales adicionales en Jammy/Postfix 3.6.4, incluida inyección real con sendmail/postdrop y upgrade desde 1.2.2.

## 1.2.2 — 2026-11-01

- Estado JSON completo servido por el propio demonio en HTTP, con host y puerto configurables y métricas numéricas para NodePing.
- Migración de las variables HTTP ausentes durante la preparación del upgrade.
- Ejecución operativa exclusivamente mediante servicio; eliminadas las opciones de programación antiguas.
- Estado atrasado recalculado por petición, JSON inválido convertido en error y restauración de la ampliación HTTP ante un upgrade fallido con el nuevo actualizador.
- 187 pruebas de integración y 9 pruebas HTTP correctas en Focal/Python 3.8 y Noble/Python 3.12, incluidos upgrades reales desde 1.0.0, 1.0.2, 1.1.0, 1.2.0 y 1.2.1.

## 1.2.1 — 2026-10-31

- Corregido el reconocimiento de cabeceras `postcat`: acepta `cola/ID` y `cola/H/.../ID`, con cualquier número de niveles de hash y con IDs cortos/largos.
- Coincidencia exacta de toda la cabecera y del ID solicitado, conservando la clasificación de colas, identidad SASL y huella de mensaje.
- Regresiones de bootstrap, mensajes previamente incompletos, cómputo sin duplicados, HOLD, liberación, reencolado, borrado y purga.
- 185 pruebas correctas por distribución; pruebas reales de colas planas/hasheadas/mixtas y upgrade desde el paquete público 1.2.0.

## 1.2.0 — 2026-10-31

- `--set-custom-limit CUENTA MAXIMO SEGUNDOS`: guarda/actualiza un límite particular y retira la cuenta de ignorados.
- `--set-ignore CUENTA`: guarda la excepción y elimina su límite particular; operaciones idempotentes y cuentas normalizadas.
- Validación de argumentos y configuración, guardado atómico privado con respaldo/caché y relectura bajo bloqueo para conservar actualizaciones simultáneas.
- Ayuda y ejemplos prácticos; resultados en el log operativo. Se mantienen las decisiones administrativas independientes sobre incidencias, bloqueos y HOLD.

## 1.1.0 — 2026-10-31

- Almacenamiento del programa trasladado a `/var/lib/outbound-spam/`: versiones, enlace current, respaldos y gestor de actualizaciones.
- Última configuración válida en `/var/lib/outbound-spam/outbound-spam.conf.last-good.json`; solo cuatro componentes de la aplicación en `/root/scripts/`.
- Migración coordinada con servicios y rollback, compatible con los actualizadores públicos 1.0.x y con sistemas de archivos distintos.
- Log privado `/var/log/outbound-spam.log`: hora/zona locales, tipo manual/servicio, estado por pasada, incidencias y resultados de acciones.
- Política logrotate automática: semanal, ocho rotaciones, compresión diferida, copytruncate y fecha.
- Paquete principal `outbound-spam-1.1.0.tar.gz` y asset histórico de compatibilidad; repositorio público conservado.
- 163 pruebas por distribución, incluidos upgrade real desde 1.0.2, migración/rollback, permisos, concurrencia de logs y rotación real.

## 1.0.2 — 2026-10-30

- Calentamiento informativo: código 0 en Monit y `--status` si no hay otra incidencia; ataques/errores reales mantienen sus códigos.
- Fechas humanas en la zona del servidor para comandos e informes, conservando epochs internos y evidencias originales.
- `--help` describe todas las operaciones y añade una guía práctica de cuentas comprometidas, HOLD y falsos positivos.
- Lanzador capaz de seguir enlaces simbólicos absolutos/relativos/encadenados desde cualquier directorio.
- Actualización conservadora de unidad systemd en upgrades: plantilla candidata, respaldo, `daemon-reload`, ExecStart y drop-ins preservados, sin iniciar/habilitar unidades paradas.
- Unidades base personalizadas se conservan y se notifican. En la transición desde 1.0.1 la plantilla operativa ya es compatible.

## 1.0.1 — 2026-10-30

- Corregida la generación de Ratelimits para `master.cf` con `discard`: se comprueba si Postfix expone cada clave antes de escribirla.
- Descubrimiento de transportes `unix` y reconocimiento conservador de agentes locales; los `pipe` genéricos no se clasifican como locales por defecto.
- Soporte de nombres con guion al reemplazar asignaciones, como `scalemail-backend`.
- Regresión con perfil ISPConfig anonimizado: relay selectivo, MX/FILTER, MySQL, SASL, milters y agentes adicionales, conservando reglas ajenas y `master.cf`.
- Conservación explícita de la configuración en instalación/upgrade: sin reescritura ni variables añadidas, comprobando bytes, inode y mtime. Las nuevas variables ausentes se notifican.
- Una actualización con configuración desaparecida se cancela en vez de regenerarla con valores por defecto.
- Verificadas 141 pruebas por distribución y el actualizador original 1.0.0 con el paquete corregido.

## 1.0.0 — 2026-10-30

- Release 1.0.0 sustituido: `--install-deps` limitado a `postfix-sqlite` sin recomendaciones, con Python/Postfix previamente instalados.

- Primera publicación estable en `github.com/feanorknd/antispam-isp`.
- Paquete instalable completo, `install.sh`, manifiesto interno y `SHA256SUMS`.
- Instalación aislada en `.antispam/releases/` y activación atómica de una versión coherente.
- `--upgrade` consulta releases estables públicos, verifica origen/checksums y extrae únicamente archivos permitidos.
- Preparación de estado bajo el mismo bloqueo de operaciones, sin acciones de correo, y copia privada anterior al cambio.
- Preservación de configuración, estado, retenciones, reservas de aviso y utilidades ajenas del servidor.
- Recuperación anterior al arranque nuevo; después de intentar iniciar se conserva el estado actual para evitar rebobinar acciones ya posibles.
- Exclusión de directorios de pruebas, logs, configuraciones privadas y referencias `etc/`/`var/` de Git y del paquete.
- 133 pruebas en la matriz de compatibilidad; incluye instalador real y preparación real de una actualización sintética 1.0.0 → 1.0.1.

## 0.3.0 — 2026-10-29

- Conservados ambos frenos: destino/nexthop 10s y transporte SMTP automático; locales/LMTP/filtro local separado exentos.
- Unidad systemd gestionada por `systemctl`, ejecutando el mismo script con `--daemon`, descanso al terminar, bloqueo de instancia y cierre ordenado.
- Parámetros de instalación/desinstalación de servicio, Monit y cron; retirada del cron gestionado al instalar el servicio.
- Lectura estable y validación de configuración en cada pasada, caché privada de última versión válida y compatibilidad con guardados de nano.
- Espera de bloqueo para administración, `--block-user`, `--purge-user-queue` y `--resolve-user`.
- Operaciones de cola con intención duradera y reconciliación tras interrupciones; resolución cancela una purga pendiente y libera su HOLD verificado.
- Copias SQLite consistentes, verificaciones y diario independiente de estado crítico con checksums y espejos; recuperación de contadores sin duplicar desde cursores alterados.
- Construcción de mapa Postfix candidato fuera de servicio y reinicio recuperable al sustituir uno corrupto, preservando bloqueos.
- Modo degradado explícito si no hay información suficiente: análisis activo y conservación de restricciones, sin automatismos peligrosos basados en inventarios desconocidos.
- Lectura acotada del log activo para no perseguir escrituras nuevas durante una pasada larga; reloj real y fecha de análisis separados de la terminación.
- 120 pruebas por distribución. Unidad verificada con `systemd-analyze`, demonio real parado por SIGTERM y mapa SQLite corrupto reparado con Postfix real.

## 0.2.0 — 2026-10-29

- Máximo de un intento de informe por cuenta cada seis horas, persistente entre reinicios y entre incidentes; informes siempre separados por cuenta.
- Reserva anterior al envío para no duplicar avisos ante un resultado incierto. Una incidencia de entrega del aviso permanece visible en Monit.
- Comparación del dominio del remitente del sobre con el de SASL y reducción del límite a un cuarto en la ventana afectada, sin incluir tráfico exclusivamente local.
- Migración de SQLite a esquema 2, con recuperación de remitentes y avisos anteriores y conservación de retenciones/bloqueos.
- Parámetros `--max-sent`, `--time-period`, `--set-limits`, `--configure-postfix`, `--yes`, `--skip-postfix`, `--outbound-delay` y `--postfix-client-limit`.
- Bloque Ratelimits delimitado, coordinación de archivo Bash y main.cf, confirmación si faltan ajustes previos, respaldo, validación, reinicio y restauración en caso de fallo.
- Freno por transporte SMTP independiente del relay/nexthop; preservación de relays selectivos y mapas de ISPConfig. Perfil sin retraso local ni límite de mensajes por IP que afecte también a locales/NAT.
- Guard remoto con clase de destinatario y rutas SQLite, incluyendo alias virtuales conocidos; migración desde el guard antiguo situado en remitentes.
- HOLD espera a que terminen las entregas locales del mensaje; se excluyen destinatarios locales ya completados de la lista pendiente.
- Distinción de filtros SMTP en loopback frente a salida real.
- Repetición semanal: tres incidentes, los dos de dominio distinto 21 minutos antes, y 56 avisos en lugar de 333.
- 85 pruebas por distribución, incluyendo reinicio/edición reales en una instancia aislada, entrega directa, dos tipos de relay selectivo, freno SMTP y entrega LMTP local sin retraso.

## 0.1.1 — 2026-10-29

- Añadido `tests/simulate_production.py`: reproducción incremental en intervalos de un minuto, estado persistente y avisos simulados, sin comandos de servidor.
- Comparados dos escenarios: metadatos de cola reconstruidos y conocimiento limitado al log disponible en cada minuto.
- Analizadas 428.953 líneas del archivo `mail.log-20261025.gz`, que cubre siete días.
- Detectados tres incidentes con el límite de 100 destinatarios externos por hora; informe público anonimizado y resultados detallados privados.
- Corregido el seguimiento de sesiones autenticadas que abandonan el envío antes de aceptar DATA: ya no quedan abiertas indefinidamente.
- Diferenciadas las transacciones en curso de los errores de cobertura de mensajes ya aceptados.
- Optimizadas las consultas de destinatarios para limitar la lectura de sobres a mensajes activos.
- Ampliada la matriz de regresión a 57 pruebas en las cinco distribuciones.

## 0.1.0 — 2026-10-29

### Repositorio y organización

- Inicializado Git en `main`.
- Excluidos `etc/`, `var/`, configuración operativa y estados privados de Git.
- Añadida exclusión equivalente para el contexto Docker.
- Creada documentación de instalación, configuración, operación, arquitectura, pruebas, versiones y tareas.

### Detección

- Entrada Bash con configuración escalar, arrays y límites asociativos.
- Motor Python 3.8+ con SQLite estándar.
- Lectura incremental binaria con cursor y eventos confirmados en una transacción.
- Recuperación de rotación por inode, gzip y truncado detectado por anclas.
- Seguimiento de escrituras tardías y presupuesto de lectura.
- Correlación por identidad SASL y generación de mensaje.
- Recuento por destinatario externo aceptado, incluidos pendientes.
- Exclusión de entregas locales, reintentos y transacciones rechazadas por milter.
- Ventanas deslizantes, cuentas ignoradas, límites particulares y detección de picos entre ejecuciones.

### Informes y contención

- Incidentes persistentes, evidencias con hasta diez mensajes y recordatorios.
- Informes mediante sendmail o SMTP directo y copia privada en disco.
- Bloqueo por usuario mediante `check_sasl_access` y mapa SQLite nativo, consultado por proxymap.
- Instalación/retirada de la integración con respaldo y validación.
- Retención por identidad del sobre, inventario propio y comprobación de huella.
- Liberación, reencolado por antigüedad, eliminación limitada a HOLD y desbloqueo independientes.
- Cambio SQL de contraseña ISPConfig SHA512-CRYPT, comparación concurrente, reintento idempotente y vaciado de caché Dovecot.

### Monitorización y comprobación

- Resumen atómico y comprobador ligero `Spam_Status` con códigos 0/1/2.
- Instalación de cron cada minuto y configuración de Monit con validación/reinicio.
- Modo replay aislado sin acciones de servidor.
- Pruebas de regresión con fallos de acciones y reinicios, más integración con Postfix real en contenedores.
- Matriz de 50 pruebas en Focal, Jammy, Noble, Debian 11 y Ubuntu 26.04; validación nativa de Monit.
- Medición sobre 449.707 líneas / 69.299.139 bytes de log de referencia, sin incorporar datos reales al repositorio.
