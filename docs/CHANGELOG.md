# Cambios efectuados

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
