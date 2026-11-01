# Documentación

| Documento | Contenido |
|---|---|
| [INSTALLATION.md](INSTALLATION.md) | Dependencias, copia de archivos, servicio, Monit y bloqueo SMTP |
| [CONFIGURATION.md](CONFIGURATION.md) | Variables, arrays, umbrales y acciones |
| [OPERATIONS.md](OPERATIONS.md) | Comandos, informes, retenciones, desbloqueo y recuperación |
| [LOGGING.md](LOGGING.md) | Registro operativo privado, hora local y rotación semanal |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Correlación, clasificación externa, cursores, SQLite y garantías |
| [TESTS.md](TESTS.md) | Pruebas reproducibles y resultados medidos |
| [SIMULATION_20261025.md](SIMULATION_20261025.md) | Reproducción minuto a minuto del archivo histórico solicitado |
| [SIMULATION_0.2.0.md](SIMULATION_0.2.0.md) | Repetición con reducción del umbral por dominio y avisos cada seis horas |
| [POSTFIX.md](POSTFIX.md) | Parámetros de ajuste, bloque Ratelimits, relays selectivos y perfiles recomendados |
| [SERVICE.md](SERVICE.md) | Servicio systemd, instalación/desinstalación y configuración en caliente |
| [RECOVERY.md](RECOVERY.md) | Verificaciones, copias y recuperación de SQLite/operaciones críticas |
| [UPGRADE.md](UPGRADE.md) | Paquete de instalación y actualizaciones desde los releases públicos |
| [VERSIONS.md](VERSIONS.md) | Versiones y matriz de compatibilidad |
| [CHANGELOG.md](CHANGELOG.md) | Cambios efectuados |
| [TASKS.md](TASKS.md) | Tareas completadas, en curso, programadas y pendientes |

## Decisiones acordadas

- Plan A: análisis periódico y contención posterior a la detección.
- Bash como interfaz y Python estándar como motor.
- Solo envíos de cuentas autenticadas correctamente.
- Solo destinatarios externos; incluir mensajes todavía pendientes de salida.
- ISPConfig de un único servidor.
- Estado privado persistente fuera de `/tmp`.
- Informes y acciones configurables por el administrador.

Los archivos de referencia de `etc/` y `var/` se utilizan localmente para estudiar el servidor y medir el analizador. No forman parte de la distribución ni de Git.
