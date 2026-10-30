# Pruebas y rendimiento

Los directorios de pruebas y datos de validación se conservan **fuera del repositorio público y del paquete instalable**. Los comandos siguientes documentan el workspace local con el que se ha validado el software; no presuponen que `tests/` exista en una descarga pública. El constructor público del paquete es `tools/build_release.py`.

## Pruebas locales

```bash
python3 -m unittest discover -s tests -v
bash -n outbound-spam.sh outbound-spam-status.sh outbound-spam.conf.example
shellcheck -S warning outbound-spam.sh lib/load_config.sh outbound-spam-status.sh outbound-spam.conf.example
```

Las pruebas crean directorios privados temporales dentro de `/tmp/opencode`, únicamente para fixtures. El almacenamiento operativo del monitor está en `/var/lib/outbound-spam`.

En un equipo de desarrollo normal la prueba nativa se omite. Solo se habilita con `OUTBOUND_SPAM_NATIVE=1`, UID 0 y detección de Docker. Sus operaciones de cola se realizan sobre una instancia temporal y aislada del contenedor.

## Matriz de distribuciones

Desde el repositorio:

```bash
docker build --build-arg BASE=ubuntu:focal -f tests/Dockerfile -t outbound-spam-tests:focal .
docker run --rm --network none outbound-spam-tests:focal

docker build --build-arg BASE=ubuntu:jammy -f tests/Dockerfile -t outbound-spam-tests:jammy .
docker run --rm --network none outbound-spam-tests:jammy

docker build --build-arg BASE=ubuntu:noble -f tests/Dockerfile -t outbound-spam-tests:noble .
docker run --rm --network none outbound-spam-tests:noble

docker build --build-arg BASE=debian:bullseye -f tests/Dockerfile -t outbound-spam-tests:debian11 .
docker run --rm --network none outbound-spam-tests:debian11

docker build --build-arg BASE=ubuntu:26.04 -f tests/Dockerfile -t outbound-spam-tests:ubuntu26 .
docker run --rm --network none outbound-spam-tests:ubuntu26
```

La construcción descarga paquetes. La ejecución no tiene acceso a red externa; el SMTP de prueba escucha solo en loopback del contenedor. Los datos de `etc/` y `var/` no se incorporan a las imágenes.

El entorno instala systemd para validar la unidad con `systemd-analyze verify`; los contenedores no arrancan systemd como PID 1. Se prueba de verdad el bucle `--daemon`, su primer trabajador y su cierre por SIGTERM; las llamadas de instalación a `systemctl` se comprueban mediante comandos simulados. Debian 11 utiliza también el archivo histórico de seguridad para mantener coherentes las dependencias de systemd.

Para inspeccionar versiones:

```bash
docker run --rm --network none outbound-spam-tests:focal python3 --version
docker run --rm --network none outbound-spam-tests:focal postconf -h mail_version
```

## Cobertura funcional

- Cuenta SASL distinta del remitente; exclusión de autenticaciones fallidas.
- Un mensaje con múltiples destinatarios y múltiples mensajes por cuenta.
- Externos/locales, copias, alias y destinatarios todavía pendientes.
- Umbral estricto, ventanas deslizantes, excepciones, límites particulares y reconocimiento.
- Picos entre ejecuciones y excesos aún actuales tras una parada.
- Reintentos de entrega y reactivaciones de `qmgr` sin doble recuento.
- IDs de cola reutilizados, bootstrap y líneas de autenticación registradas con retraso.
- Rechazos de milter que no deben contar como correo aceptado.
- RSET/DATA fallido, DATA/BDAT correcto con `qmgr` retrasado y transacciones todavía en curso.
- Lectura incremental sin cambios, líneas parciales, rollback, rotación, gzip, copytruncate y presupuesto.
- Fallos independientes de acciones; informe aunque falle el bloqueo.
- Reserva de seis horas por cuenta, fallos inciertos de correo, reapertura y reinicio sin duplicar avisos.
- Umbral al cuarto, dominios equivalentes, remitente vacío y tráfico local que no reduce la cuota.
- Migración del esquema anterior y del guard de remitente hacia contención exclusivamente remota.
- Retención por identidad SASL, retenciones ajenas, recuperación de intención y rechazo de un ID sustituido.
- Liberación manual que impide volver a retener el mismo incidente.
- Actualización SQL de contraseña sin repetir el cambio tras un fallo de caché; formato no soportado.
- Avisos iniciales/recordatorios, configuración inválida y permisos de estado.
- Instalación de Monit con rollback y lectura real de códigos/antigüedad.
- CLI Bash/Python completa y sintaxis compatible con Python 3.8.
- Ajustes delimitados, duplicados/continuaciones, pregunta/negativa, configuración sin Postfix, validación y recuperación tras fallo de arranque.
- Postfix real: edición/reinicio de una instancia temporal, sobre SASL, expansión a externos, bloqueo remoto y envío local desde la misma sesión.
- Entrega SMTP directa, relay por remitente y relay por `transport_maps`, con `relayhost` vacío y freno global medido entre destinos.
- Entregas LMTP reales sin esperar el retraso del transporte SMTP.
- Monit real: validación de la configuración generada, sin arrancar su servicio.
- Simulación histórica: cola pendiente detectada antes de entrega, copia local excluida, avisos sin efectos de servidor y ausencia de destinatarios inventados.
- Servicio secuencial, bloqueo de segunda instancia, migración de cron y desinstalación de servicio/Monit conservando estado.
- Nano: sustitución de inode, escritura parcial, archivo vacío, error sintáctico/semántico y última configuración válida.
- Copias SQLite verificadas, corrupción real, pérdida del fichero, espejo/diario, valores críticos alterados y cursor sin doble recuento.
- Credencial objetivo y propiedad de HOLD conservadas al restaurar; reserva de seis horas no reiniciada.
- Liberación interrumpida, purga/resolve y prevención de borrado de otra identidad.
- Mapa Postfix real reconstruido fuera de servicio y entregas directas/selectivas sujetas simultáneamente a ambos frenos.
- Paquete reproducible, manifiesto/hashes, extracción sin rutas de escape/enlaces y ausencia de datos de pruebas.
- Instalación nueva real, distribución versionada, configuración conservada y estado retenido en preparación real de upgrade.
- Regresión con master completo (incluido discard) y agentes ISPConfig: comprobación de claves expuestas, nombres con guion y conservación de reglas MX/FILTER, MySQL, SASL y relay.
- Configuración privada preservada en contenido, inode, mtime y permisos; variables nuevas solo notificadas y configuración desaparecida no regenerada durante upgrade.
- Actualización mediante el código real del release público 1.0.0, ejecutada sin red con sus assets de prueba y el paquete nuevo.
- Consulta de release estable, versión ya actualizada, checksum erróneo, fallo de preparación y fallo de arranque sin rebobinar acciones.

El cambio de contraseña contra la base ISPConfig real y el reinicio del Monit de destino forman parte de la validación de despliegue, no de los resultados simulados.

## Resultado registrado

Fecha de la distribución estable: **2026-10-30**.

Resultado actual: [TEST_RESULTS_1.0.1.json](TEST_RESULTS_1.0.1.json). Se conservan los de [1.0.0](TEST_RESULTS_1.0.0.json), [0.3.0](TEST_RESULTS_0.3.0.json), [0.2.0](TEST_RESULTS_0.2.0.json), [0.1.1](TEST_RESULTS_0.1.1.json) y [0.1.0](TEST_RESULTS.json).

| Entorno | Resultado |
|---|---|
| Ubuntu Focal | 141 pruebas, correctas |
| Ubuntu Jammy | 141 pruebas, correctas |
| Ubuntu Noble | 141 pruebas, correctas |
| Debian 11 | 141 pruebas, correctas |
| Ubuntu 26.04 / Python 3.14 | 141 pruebas, correctas |
| Bash/ShellCheck local | Sin errores ni advertencias de nivel warning |

## Rendimiento con el log de referencia

Comando de medición offline, con una fecha posterior al último evento de la muestra:

```bash
python3 tests/benchmark.py var/log/mail.log --now 1793275200
```

La herramienta elimina su estado temporal al terminar e imprime solo métricas agregadas. `--diagnostics` añade IDs y muestras de transacciones incompletas; su salida puede contener datos de correo reales y no debe incorporarse al historial.

Resultado de referencia 0.3.0 en Python 3.12.3:

| Métrica | Resultado aproximado |
|---|---:|
| Tamaño de log | 69.299.139 bytes |
| Líneas | 449.707 |
| Lectura inicial completa | 3,65 s |
| Segunda lectura sin cambios | 0,0084 s |
| Bytes leídos en la segunda ejecución | 0 |
| Pico de memoria del proceso | 29 MiB |
| Base SQLite tras checkpoint | 1,8 MB |
| Mensajes autenticados registrados | 486 |
| Eventos de destinatarios externos aceptados | 642 |
| Transacciones no aceptadas por milter | 24 |
| Huecos de cobertura al final de la muestra | 0 |

Estos tiempos corresponden a lectura y evaluación offline con caché de disco. No incluyen validación/caché de configuración, verificaciones y copias de recuperación ni acciones externas. El coste operativo añade consultas selectivas a la cola/mapas y las acciones elegidas. El bootstrap y la contención pueden recorrer la cola completa: su duración depende del spool, no solo del log.

La simulación incremental actual está descrita en [SIMULATION_0.2.0.md](SIMULATION_0.2.0.md); el [análisis inicial](SIMULATION_20261025.md) queda como referencia histórica.
