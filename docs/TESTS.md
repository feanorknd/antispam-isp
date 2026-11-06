# Pruebas y rendimiento

Los directorios de pruebas y datos de validación se conservan **fuera del repositorio público y del paquete instalable**. Los comandos siguientes documentan el workspace local con el que se ha validado el software; no presuponen que `tests/` exista en una descarga pública. El constructor público del paquete es `tools/build_release.py`.

## Pruebas locales

Versión estable 2.0: ver [RELEASE_2.0.0.md](RELEASE_2.0.0.md) para histórico, corrupción aislada, integración Nginx, actualizadores publicados 1.3.0–1.3.4 y presupuesto de CPU. Herramientas: `tools/test_release200.py`, `tools/test_analytics.py`, `tools/test_dashboard_proxy.py`, `tools/test_release150.py` y `tools/benchmark_analytics.py`. La matriz 1.3.x ejecuta el CLI antiguo y arranca el demonio real tras el upgrade. `LIVE_RELEASE=1` permite comprobar además la descarga oficial después de publicar, sin sustituir el downloader.

Resultados 2.0.0: [TEST_RESULTS_2.0.0.json](TEST_RESULTS_2.0.0.json). Los once escenarios de upgrade por entorno incluyen los cinco releases 1.3.x con servicio activo/parado y la desactivación explícita de estadísticas. Las descargas se sustituyen por los assets del candidato para la matriz aislada; los validadores, configuración y CLI de los paquetes anteriores son reales. Se comprueba además el nuevo demonio y el contenido integrado del dashboard.

Validación local 1.3.4 (2026-11-04): **224 pruebas: 205 correctas y 19 omitidas por requisitos del entorno**, sin fallos. Además, 18 de dominios/PSL, 11 de configuración y 9 HTTP públicas correctas. Incluye presentación de ayuda, salida sin argumentos, color y fallback, separadores de casos prácticos, inspección por nombre/UID y regresiones de distribución/upgrade con comandos simulados. Detalles: [RELEASE_1.3.4.md](RELEASE_1.3.4.md).

Validación 1.3.3 (2026-11-04): **221 pruebas de integración + 18 de dominios/PSL + 11 de configuración + 9 HTTP por entorno** en Focal/Python 3.8 y Noble/Python 3.12, sin errores ni omisiones. Incluye upgrade desde el release público 1.3.2, doctor e inspector con padres registrables, envío real de sendmail desde ese padre, cuotas y contención con Postfix real. Resultados: [TEST_RESULTS_1.3.3.json](TEST_RESULTS_1.3.3.json).

Las regresiones públicas de dominios incluyen dos subdominios con un único padre, sufijos compuestos `com.es`/`co.uk`, coincidencia PSL más larga, comodines/excepciones, sufijos privados e IDNA. La lista PSL se distribuye en el paquete y las pruebas de ejecución se realizan sin red.

Validación 1.3.2 (2026-11-03): **219 pruebas completas de integración + 10 de dominios nullable + 11 de configuración + 9 HTTP por entorno** en Focal y Noble, sin errores ni omisiones. El escenario nativo utiliza el actualizador público 1.3.1, comprueba doctor e inspector sobre un alias con usuario SQL NULL y verifica después cuotas y contención con Postfix real. Resultado: [TEST_RESULTS_1.3.2.json](TEST_RESULTS_1.3.2.json).

Pruebas públicas: `python3 tools/test_websites.py -v`. Reproducen los pares alias/padre 33→32, 44→5 y 20→6 con dominios anonimizados y usuarios no derivados de los IDs. Cubren NULL frente a usuario vacío/literal, herencia encadenada, pools independientes, foreign, errores de campo/identificador/IDNA y reintentos de lectura. Se ejecutó además la consulta real en MariaDB 10.11.19 con una tabla nullable equivalente al esquema reportado; los alias se asociaron correctamente a sus usuarios padre.

Publicación v1.3.2 comprobada sin autenticación con los actualizadores 1.0.2, 1.3.0 y 1.3.1: ambos paquetes descargados validan checksums y manifiestos idénticos. Se ejecutó la resolución de un alias NULL con el código de cada descarga. La reconstrucción reproduce `7a65fb5d8e9e9c5111fe65409b62d46b79d55a0ebf94eefbcb74ec6784227e30` para el paquete principal. Esta anotación es posterior a la documentación fijada por la etiqueta.

Validación 1.3.1 (2026-11-03): **218 pruebas de integración verificadas + 11 de configuración + 9 HTTP por entorno** en Focal y Noble. Se ejecutó la suite completa de 217 pruebas y una regresión adicional del actualizador público 1.2.2; se repitió además la prueba con Postfix real usando el actualizador 1.3.0 y una segunda llamada a `--upgrade` sin versión nueva. Resultado: [TEST_RESULTS_1.3.1.json](TEST_RESULTS_1.3.1.json).

El contrato de ampliación automática es público y se comprueba con `python3 tools/test_config_completion.py -v`: todas las variables del motor están en el ejemplo y en el archivo completado; se conservan valores y arrays; se añaden futuras variables; no hay duplicación ni reescritura innecesaria; se prueba caché, permisos, backup, concurrencia y rollback. Los escenarios de distribución verifican también el ejemplo instalado y una configuración privada parcial preexistente.

Release v1.3.1 comprobado sin autenticación con los actualizadores públicos 1.0.2, 1.1.0, 1.2.2 y 1.3.0: descubren la última versión, descargan el asset correspondiente y verifican checksums y manifiestos idénticos. El código descargado expone la migración y el inventario completo. Reconstrucción reproducible del paquete principal: `7a22bee90b6c65e75165907c42c32d05b333360cf888e3b00324f80f82f110db`. Los assets fijan la documentación de la etiqueta; esta anotación de comprobación es posterior.

Validación 1.3.0 (2026-11-03): **213 pruebas completas + 9 HTTP** en Focal y Noble, y **25 pruebas locales** en Jammy/Postfix 3.6.4. Resultado: [TEST_RESULTS_1.3.0.json](TEST_RESULTS_1.3.0.json). La nueva cobertura incluye UID/PID, dominios y foreign automáticos, cuota externa, ausencia de reset local, ACL hash sin reload por incidente, rollback, recuperación y operación real de sendmail/postdrop con UID 5009. La prueba nativa actualiza mediante el actualizador público 1.2.2 e inspecciona la identidad desde el CLI instalado.

Las pruebas de ISPConfig simulan sus filas SQL, incluyendo alias, descendientes, auto-subdominios y pools independientes. No se dispone de ISPConfig de producción en el entorno de desarrollo. Para contrastar UID/login/home, esquema y dominios en un servidor real, véase la batería de [LOCAL_SUBMISSION.md](LOCAL_SUBMISSION.md#comprobaciones-en-un-ispconfig-real).

En 1.3.2 se añadió la comprobación de protocolo y esquema con MariaDB real descrita arriba; no equivale a una instalación completa de ISPConfig.

Publicación v1.3.0 verificada sin autenticación: los actualizadores públicos 1.0.2, 1.1.0, 1.2.1 y 1.2.2 descubren el release estable, descargan su paquete correspondiente y validan checksums y manifiestos idénticos. El código descargado carga las nuevas clases y el CLI. La reconstrucción desde el commit de la etiqueta reproduce `296a1ef23677675a145c1a00ec7e03b6850f45df2170a18e9b64f246f0702f94` para el paquete principal. Los assets contienen la documentación fijada en esa etiqueta; `main` incorpora esta anotación posterior de verificación.

Para el endpoint y la migración HTTP de 1.2.2, ejecutar `python3 tools/test_http_status.py -v`. Las nueve pruebas cubren estado completo, errores, antigüedad, valores no finitos, concurrencia, puerto ocupado, migración idempotente, CLI y ciclo de vida del demonio.

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

Para incluir la migración entre discos, añadir a `docker run` las opciones `--tmpfs /migration-data:mode=0700 -e OUTBOUND_SPAM_CROSSFS=/migration-data`. Esta variante se ha utilizado en las matrices de 1.1.0 y 1.2.0. Las imágenes incluyen logrotate para validar la política y una rotación real.

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
- Configuración privada conservada y, desde 1.3.1, completada automáticamente; contenido previo y valores preservados, inode/mtime estables cuando no falta ninguna variable, permisos privados y ausencia de regeneración de una configuración desaparecida.
- Actualización mediante el código real del release público 1.0.0, ejecutada sin red con sus assets de prueba y el paquete nuevo.
- Calentamiento solo informativo (probe real de Monit devuelve 0), sin esconder ataques ni errores.
- Fechas locales CET/CEST y UTC, preservando los valores internos de máquina.
- Ayuda con comandos explicados y guía práctica de incidencias.
- Enlaces de comando absolutos, relativos, encadenados y buscados por PATH, con configuración predeterminada desde otro directorio.
- Cambio de plantilla systemd durante upgrade, conservación de ExecStart/drop-ins/unidades personalizadas y restauración ante fallo de daemon-reload.
- Consulta de release estable, versión ya actualizada, checksum erróneo, fallo de preparación y fallo de arranque sin rebobinar acciones.
- Distribución 1.1.0 con cuatro puntos de entrada, datos/código privados en `/var/lib/outbound-spam/`, migración de caché y rollback de rutas.
- Actualizador y CLI reales del release público 1.0.2 con servicio activo/parado; traslado entre sistemas de archivos diferentes mediante tmpfs privado.
- Log con hora/zona del servidor, tipo manual/servicio, resultados de acciones, exclusión de hashes y entradas concurrentes íntegras.
- Rechazo de enlaces simbólicos/duros en el log; validación y rotación real con logrotate, conservando 0600 y escritura posterior.
- Administración de límites/excepciones por cuenta: reemplazo, prioridad mutua, normalización, idempotencia y conservación del resto del perfil.
- Validación de dirección/argumentos/retención, arrays Bash multilínea, escape de caracteres de cuenta y rechazo de bloques inválidos.
- Fallo de guardado de caché con restauración, detección de edición concurrente y dos procesos CLI reales esperando el mismo bloqueo sin perder cambios.
- Actualización de última configuración válida y logs sin credenciales; las órdenes de reglas por cuenta no ejecutan Postfix ni alteran incidentes existentes.
- Upgrade mediante el CLI y actualizador del paquete público 1.1.0, con unidad activa/parada, configuración preservada y uso posterior de ambos comandos nuevos en la instalación actualizada.
- Cabeceras de sobre con cero/uno/varios niveles de hash, IDs cortos/largos, coincidencia exacta del ID y rechazo de rutas/cabeceras inválidas.
- Bootstrap de colas con hash, mensajes previamente incompletos completados sin reiniciar estado y cómputo sin duplicados.
- Postfix real: CLI de análisis y operaciones de HOLD/liberación/reencolado/borrado/purga con colas planas, con uno/dos/tres niveles y mixtas, e IDs cortos/largos en cada disposición.
- Upgrade mediante el CLI y actualizador del paquete público 1.2.0, con servicio activo/parado y configuración preservada.

El cambio de contraseña contra la base ISPConfig real y el reinicio del Monit de destino forman parte de la validación de despliegue, no de los resultados simulados.

## Resultado registrado

Fecha de validación de 1.2.2: **2026-11-01**. Resultado: [TEST_RESULTS_1.2.2.json](TEST_RESULTS_1.2.2.json).

Focal/Python 3.8 y Noble/Python 3.12: 187 pruebas completas de integración sin omisiones y 9 pruebas HTTP correctas por entorno. Incluye actualizadores de releases públicos hasta 1.2.1 y petición HTTP al demonio real tras su primera pasada. Bash, ShellCheck y `git diff --check` correctos. La compatibilidad documentada de otras distribuciones corresponde a la matriz histórica siguiente.

Después de publicar v1.2.2 se verificaron sin autenticación la API `/releases/latest`, las descargas, ambos checksums y los manifiestos idénticos con los actualizadores públicos 1.0.2, 1.1.0 y 1.2.1. Todos descubren 1.2.2 y el código descargado entrega el JSON de error esperado cuando no hay estado.

### Resultado histórico 1.2.1 (2026-10-31)

Resultado actual: [TEST_RESULTS_1.2.1.json](TEST_RESULTS_1.2.1.json). Se conservan los de [1.2.0](TEST_RESULTS_1.2.0.json), [1.1.0](TEST_RESULTS_1.1.0.json), [1.0.2](TEST_RESULTS_1.0.2.json), [1.0.1](TEST_RESULTS_1.0.1.json), [1.0.0](TEST_RESULTS_1.0.0.json), [0.3.0](TEST_RESULTS_0.3.0.json), [0.2.0](TEST_RESULTS_0.2.0.json), [0.1.1](TEST_RESULTS_0.1.1.json) y [0.1.0](TEST_RESULTS.json).

| Entorno | Resultado |
|---|---|
| Ubuntu Focal | 185 pruebas, correctas |
| Ubuntu Jammy | 185 pruebas, correctas |
| Ubuntu Noble | 185 pruebas, correctas |
| Debian 11 | 185 pruebas, correctas |
| Ubuntu 26.04 / Python 3.14 | 185 pruebas, correctas |
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
