# Versiones y compatibilidad

## Distribución

| Elemento | Versión |
|---|---|
| Aplicación | 1.1.0 |
| Esquema SQLite privado (`user_version`) | 3 |
| Python mínimo | 3.8 |
| Bash utilizado | 5.0 o posterior en las distribuciones verificadas |
| Postfix requerido | 3.1+ para `postqueue -j`; integración con `postfix-sqlite` |

`VERSION` y `--version` deben coincidir; las pruebas lo comprueban. Las futuras modificaciones incompatibles del esquema deberán introducir una migración y actualizar este documento.

## Matriz de la distribución 1.1.0

| Imagen | Python | Postfix | Resultado |
|---|---:|---:|---|
| Ubuntu Focal 20.04 | 3.8.10 | 3.4.13 | 163 pruebas correctas |
| Ubuntu Jammy 22.04 | 3.10.12 | 3.6.4 | 163 pruebas correctas |
| Ubuntu Noble 24.04 | 3.12.3 | 3.8.6 | 163 pruebas correctas |
| Debian 11 Bullseye | 3.9.2 | 3.5.25 | 163 pruebas correctas |
| Ubuntu 26.04 | 3.14.4 | 3.10.6 | 163 pruebas correctas |

La versión 0.2.0 migra el esquema 1 a 2: añade remitente y diferencia de dominio, motivo/umbral base de los incidentes y reservas de aviso por cuenta. Recupera los remitentes de las líneas de cola conservadas e importa los últimos intentos/avisos anteriores para respetar la separación de seis horas. Se preservan inventarios de HOLD y bloqueos. Los análisis históricos de otra versión deben reproducirse en un directorio nuevo.

Al actualizar, instalar también `lib/postfix_settings.py`. Si existía el guard 0.1.x, ejecutar `--install-postfix-guard` para migrarlo a bloqueo por destinatario remoto y conservar la entrega local.

La versión 0.3.0 añade `queue_ops` y migra sin borrar los inventarios ni las reservas de avisos. Instalar también `lib/runtime.py`, `lib/state_recovery.py` y `lib/load_config.sh`. `--install-service` sustituye la programación gestionada de cron por una unidad systemd. El registro crítico se inicializa desde el estado verificado al abrir la primera operación.

La versión estable 1.0.0 conserva el esquema 3, añade instalación versionada y `--upgrade`, y utiliza un manifiesto de distribución con `upgrade_api=1`. Los releases están etiquetados como `vX.Y.Z`; el archivo `VERSION` usa `X.Y.Z`.

1.0.1 corrige Ratelimits para distintas definiciones de master/main sin cambiar esquema ni API de actualización. La prueba de compatibilidad utiliza el paquete público 1.0.0 y su propio actualizador para desplegar 1.0.1, con configuración privada preservada.

1.0.2 conserva esquema 3 y upgrade_api 1. Añade `service_unit_api=1`, presentación local de fechas, calentamiento informativo, ayuda y resolución de enlaces. El archivo interno de estado conserva timestamps numéricos para compatibilidad con Monit y sus cálculos.

1.1.0 conserva esquema y API. Reorganiza el almacenamiento en `/var/lib/outbound-spam/`, migra la distribución histórica y añade logs privados y rotación. El paquete principal utiliza el nombre outbound-spam y se mantiene un asset compatible con los actualizadores 1.0.x.

Cada contenedor ejecuta también la prueba de Postfix real: alias, copia local, cola HOLD, identidad SASL, clasificación y bloqueo durante una sesión abierta. La identidad SASL de esa prueba se introduce por `XCLIENT LOGIN` en una instancia aislada, con SASL habilitado; no es una prueba de contraseña contra Dovecot/ISPConfig.

Se valida además la configuración generada con el binario real de Monit, sin arrancar su servicio.

Las actualizaciones de paquetes pueden cambiar las revisiones. Consultar los comandos de [TESTS.md](TESTS.md) para obtener la versión exacta de una imagen reconstruida. Debian 11 ya utiliza el archivo histórico en el Dockerfile de pruebas; esa decisión afecta al entorno de reproducción, no al servidor de producción.

El código evita módulos retirados como `crypt` y dependencias pip. La compatibilidad con distribuciones posteriores se basa en esas interfaces estándar, y debe confirmarse con la misma matriz al incorporar nuevas versiones.
