# Tareas y programación

Actualizado: **2026-10-31**, versión 1.2.1.

## Completadas

- [x] Revisar la configuración de referencia y fijar plan A.
- [x] Inicializar Git y excluir `etc/` y `var/`.
- [x] Implementar Bash, configuración y motor Python/SQLite.
- [x] Recuento externo con correlación de mensajes, reintentos y cola pendiente.
- [x] Lectura incremental, rotación, compresión, truncado y fallos transaccionales.
- [x] Informes, recordatorios y evidencias persistentes.
- [x] Integración SMTP por usuario con SQLite/proxymap, sin servicio de cuotas nuevo.
- [x] Retención, liberación, eliminación, desbloqueo y reconocimiento.
- [x] Cambio SQL de contraseña y recuperación de una ejecución interrumpida.
- [x] Comprobador e instalación de Monit y cron.
- [x] Pruebas con Postfix real en Focal, Jammy, Noble y Debian 11.
- [x] Validación adicional de una distribución posterior: Ubuntu 26.04 / Python 3.14.
- [x] Validación nativa de la configuración generada con Monit real en las cinco distribuciones.
- [x] Medición offline sobre el log de referencia.
- [x] Redactar documentación, historial y versiones.
- [x] Revisión final de documentación, resultados y exclusiones de Git de la entrega inicial.
- [x] Reproducir `mail.log-20261025.gz` durante 10.081 ejecuciones virtuales por escenario.
- [x] Comparar detección con metadatos de cola y detección limitada a las líneas ya registradas.
- [x] Corregir sesiones abandonadas y falsos errores de cobertura durante DATA en curso.
- [x] Verificar 57 pruebas en las cinco distribuciones después de la corrección.
- [x] Documentar incidentes, sensibilidad de umbrales y limitaciones de la reconstrucción.
- [x] Separar informes por cuenta y reservar intentos con mínimo de seis horas, incluso al reabrir incidentes.
- [x] Reducir al cuarto el umbral por dominio remitente SMTP distinto y documentar su alcance por ventana.
- [x] Implementar ajustes por parámetros, persistencia, bloque delimitado de main.cf, pregunta, respaldo, validación y reinicio.
- [x] Implementar el freno por transporte, sin dependencia de relay predeterminado ni modificación de mapas selectivos.
- [x] Preservar entrega local mediante guard por destinatario y HOLD solo con pendientes externos.
- [x] Migrar estado anterior y guard 0.1.x; probar importación de avisos y conservación de inventarios.
- [x] Probar 85 casos en Postfix 3.4.13, 3.5.25, 3.6.4, 3.8.6 y 3.10.6.
- [x] Repetir el archivo semanal con la política 0.2.0: tres incidentes, 56 avisos y detección adelantada de B/C.
- [x] Mantener ambos frenos: 10 s por destino y freno automático por transporte.
- [x] Instalar/desinstalar servicio systemd, monitor Monit y cron propio por parámetros.
- [x] Ejecutar el mismo script con descanso posterior a cada pasada, cierre por SIGTERM y exclusión con comandos manuales.
- [x] Admitir guardados de nano en sitio o por sustitución, con caché de última configuración válida.
- [x] Purgar salida por identidad SASL y resolver falsos positivos de forma coordinada y recuperable.
- [x] Verificar/restaurar SQLite, cursores, historial crítico, reservas de aviso y mapa Postfix.
- [x] Probar fallos de datos/índices/diario, liberación interrumpida, disco lleno simulado y pérdida de referencias.
- [x] Ampliar a 120 pruebas por distribución, incluida validación real de unidad systemd y arranque/parada del demonio.
- [x] Preparar instalador, paquete reproducible con checksums y actualizador de releases públicos.
- [x] Excluir pruebas, configuración y referencias privadas de la publicación.
- [x] Verificar 133 pruebas en las cinco distribuciones y una preparación real de actualización.
- [x] Subir el código a `git@github.com:feanorknd/antispam-isp.git` y etiquetar `v1.0.0`.
- [x] Publicar el release estable 1.0.0 con paquete instalable y `SHA256SUMS`.
- [x] Verificar la API y la descarga públicas sin autenticación, con coincidencia del SHA-256.
- [x] Limitar `--install-deps` a `postfix-sqlite` sin recomendaciones, requiriendo Python y Postfix preinstalados.
- [x] Eliminar y recrear el release y la etiqueta 1.0.0 con el instalador corregido; verificar el archivo público.
- [x] Reproducir y corregir `discard_destination_rate_delay` con el master estándar y perfiles ISPConfig.
- [x] Reconocer los transportes y parámetros disponibles de cada instalación sin asumir un master/main fijo.
- [x] Verificar 141 pruebas por distribución y una actualización con el actualizador real 1.0.0.
- [x] Preservar configuración privada sin reescritura/variables añadidas; notificar únicamente nuevos nombres ausentes.
- [x] Publicar 1.0.1 y verificar API/paquete públicos para actualizar desde 1.0.0.
- [x] Mostrar calentamiento informativo sin error de Monit y sin ocultar incidentes/errores reales.
- [x] Fechas humanas en la zona horaria del servidor para comandos e informes.
- [x] Ayuda completa y guía práctica para spam, HOLD y falsos positivos.
- [x] Resolver enlaces simbólicos del comando desde cualquier directorio.
- [x] Renovar unidades systemd propias intactas durante upgrade y preservar personalizaciones/estado parado.
- [x] Verificar 153 pruebas en las cinco distribuciones, incluidos cambios de zona horaria.
- [x] Publicar 1.0.2 y comprobar API/descarga públicas desde 1.0.1.
- [x] Reunir código, versiones, respaldos y última configuración válida en `/var/lib/outbound-spam/`.
- [x] Migrar desde el actualizador real 1.0.2, con servicio activo/parado y discos distintos.
- [x] Añadir logs privados manuales/de servicio y política semanal de logrotate.
- [x] Validar 163 pruebas por distribución, incluyendo rotación real y rollback de rutas.
- [x] Publicar 1.1.0 y verificar sin autenticación ambos paquetes, checksums y descubrimiento mediante el actualizador público 1.0.2.
- [x] Administrar límites particulares e ignorados con `--set-custom-limit` y `--set-ignore`, conservando las reglas de las demás cuentas.
- [x] Validar entradas, prioridades, idempotencia, caché y restauración, con bloqueo y registro de resultados.
- [x] Incorporar ambos comandos y ejemplos prácticos en la ayuda y la documentación.
- [x] Verificar 176 pruebas en las cinco distribuciones, incluido el upgrade público desde 1.1.0 y el uso de los nuevos comandos en la instalación actualizada.
- [x] Publicar 1.2.0 y comprobar descargas sin autenticación, checksums, manifiestos y descubrimiento con los actualizadores públicos 1.1.0 y 1.0.2.
- [x] Corregir lectura de sobres con subdirectorios Postfix, con ID exacto y profundidad independiente de la configuración.
- [x] Verificar 185 pruebas por distribución, incluidas colas reales y upgrade desde 1.2.0.
- [x] Publicar 1.2.1 y verificar sin autenticación el descubrimiento desde 1.2.0/1.1.0/1.0.2, los checksums y el parser de los paquetes descargados.

## En ejecución

Ninguna tarea de publicación pendiente. Release: https://github.com/feanorknd/antispam-isp/releases/tag/v1.2.1 .

## Programadas por el software, al instalarlo en el servidor

| Tarea | Frecuencia | Responsable |
|---|---|---|
| Lectura y evaluación del log | Pasada completa + descanso de 60 s | `outbound-spam.service` |
| Reintentos de contención fallida | En la siguiente ejecución | Motor del analizador |
| Reintentos de aviso fallido | Nunca antes de seis horas para esa cuenta | Motor del analizador |
| Revisión de mensajes nuevos de cuentas en incidente | Cada ejecución si HOLD está activo | Motor del analizador |
| Recordatorios de incidentes | Según `reminder_interval`, mínimo seis horas por cuenta | Motor del analizador |
| Actualización de rutas del guard | Al bloquear y periódicamente con bloqueos activos | Motor del analizador |
| Comprobación de estado/antigüedad | Cada ciclo de Monit | `Spam_Status` |
| Purga de correlaciones terminadas | Cada ejecución, según retención | Motor del analizador |
| Copias verificadas de SQLite | Según `backup_interval` | Gestor de recuperación |
| Diario crítico y reconciliación | Antes/después de efectos externos | Gestor de recuperación |
| Rotación del log operativo | Semanal, ocho copias | Cron/timer de logrotate del servidor |

Estas tareas se han probado en el entorno de desarrollo; su programación operativa requiere ejecutar los instaladores en el servidor de destino.

## Pendientes de despliegue

- [ ] Configurar destinatarios de informes, remitente, límites y excepciones reales.
- [ ] Ejecutar `--doctor` en el servidor ISPConfig de destino.
- [ ] Validar sus transportes y mapas especiales con mensajes de prueba locales/externos.
- [ ] Validar cambio de contraseña en una cuenta de prueba de la instalación ISPConfig real.
- [ ] Instalar la integración SMTP, el servicio systemd y Monit en ese servidor.
- [ ] Sustituir el cron gestionado por el servicio systemd y retirar entradas manuales duplicadas.
- [ ] Al actualizar 0.1.x, instalar el módulo de ajustes y volver a ejecutar el instalador del guard.
- [ ] Al actualizar a 0.3.0, copiar todos los módulos/runtime y conservar el estado y su directorio de recuperación.
- [ ] Comprobar el canal de notificación elegido y su tiempo de recepción.
- [ ] Aplicar el perfil coordinado y ajustar su capacidad al tráfico legítimo agregado de cada servidor.

## Mejoras identificadas

- [ ] Añadir a la matriz distribuciones posteriores cuando se incorporen formalmente al soporte.
- [ ] Ampliar los formatos de contraseña cuando exista una instalación objetivo que los necesite.
- [ ] Añadir trazabilidad específica para filtros posteriores a cola y reenvíos con reinyección, si se utilizan.
- [ ] Política configurable de archivo/purga de incidentes e informes cerrados.
- [ ] Automatización CI remota cuando exista un remoto y se solicite su integración.

No hay un servicio de cuotas en tiempo real programado: corresponde al plan B, que no fue el elegido.
