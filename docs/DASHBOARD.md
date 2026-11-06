# Dashboard e histórico · versión estable 2.0

Versión estable **2.0.0**, rama `main`, basada en las pruebas 1.5.0/1.5.1. El tar.gz se genera con `python3 tools/build_release.py --output dist/2.0.0`.

La rama `1.5` conserva la última versión de pruebas y la rama `1.3` mantiene las correcciones de la línea anterior. El release `v1.3.4` sigue disponible para recuperación.

## Instalación o actualización desde el release actual

Desde cualquier versión 1.3.x instalada mediante releases:

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --doctor
sudo systemctl status outbound-spam.service
```

El actualizador incorpora las variables ausentes, deja `analytics_enabled=true` por defecto y reinicia únicamente el servicio que estuviera activo. Una asignación existente `analytics_enabled=false` se conserva. No se necesita instalar una versión intermedia ni ejecutar de nuevo los instaladores de guards/servicio.

También se puede copiar `outbound-spam-2.0.0.tar.gz` y `SHA256SUMS` a un directorio de preparación. En ese directorio (válido igualmente desde las versiones de pruebas 1.5.x):

```bash
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf outbound-spam-2.0.0.tar.gz
sudo ./outbound-spam-2.0.0/install.sh --target /root/scripts --config /root/scripts/outbound-spam.conf
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --doctor
sudo systemctl status outbound-spam.service
```

Usar las rutas reales si son distintas. El instalador del candidato admite una instalación existente: conserva configuración, identidad de la instalación, estado operativo y acciones. Completa las nuevas variables, toma respaldos y vuelve a arrancar únicamente el servicio que estuviera activo. No necesita reinstalar guards ni ejecutar `--configure-postfix` para habilitar estadísticas. El esquema operativo continúa en **4**; se añaden tablas auxiliares sin cambiar los campos que necesita 1.3.4.

En una instalación nueva se puede utilizar `--install-deps` si falta `postfix-sqlite`. Después configurar las direcciones y acciones reales y seguir [INSTALLATION.md](INSTALLATION.md). El instalador inicial deja los avisos desactivados.

**El `--upgrade` publicado de 1.3.0–1.3.4 admite el paquete 2.0.0.** Para conservar esa compatibilidad, el asset integra los fuentes del trabajador, la API y los recursos web dentro de `lib/runtime.py`, uno de los componentes admitidos por los validadores anteriores. El manifiesto mantiene API 1 y registra también los hashes de los componentes integrados. En el repositorio se mantienen los fuentes separados; no se extrae código sobre el directorio de estado ni se requieren pip/npm.

## Configuración

```bash
analytics_enabled=true
analytics_outbox_mb=64
```

La retención estadística es **60 días**, incluyendo agregados. `retention_days` sigue siendo la retención del monitor operativo, independiente. Al activar/desactivar `analytics_enabled` hay que reiniciar `outbound-spam.service` para coordinar HTTP, capturador y trabajador. Con `false` no se ofrece el dashboard ni se genera trabajo estadístico nuevo; los datos existentes se conservan.

El histórico comienza con la nueva captura; no relee automáticamente todos los logs antiguos. Los usuarios aparecen a medida que se observan y se mantienen mientras tengan datos en el histórico conservado.

## Nginx: conservar /spam-status y añadir /spam-status/

Dentro del `server` que ya utiliza el include:

```nginx
location = /spam-status {
    auth_basic "Restricted access";
    auth_basic_user_file /etc/nginx/auth/outbound-spam;
    proxy_pass http://127.0.0.1:23456/;
    proxy_buffering off;
}

location ^~ /spam-status/ {
    auth_basic "Restricted access";
    auth_basic_user_file /etc/nginx/auth/outbound-spam;
    proxy_pass http://127.0.0.1:23456/dashboard/;
    proxy_buffering off;
}
```

Las barras finales son importantes. `/spam-status` mantiene su JSON y `/spam-status/` sirve la web. Nginx transforma `/spam-status/api/overview?...` en `/dashboard/api/overview?...` y `/spam-status/assets/app.js` en `/dashboard/assets/app.js`. Toda la interfaz usa URLs relativas: no escapa del prefijo ni requiere otro puerto. Ambos bloques protegen también sus recursos y API con la autenticación existente.

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -fsS -u usuario https://su-dominio/spam-status
```

Abrir `https://su-dominio/spam-status/` en el navegador. Directamente en el servicio también funciona `http://127.0.0.1:23456/dashboard/`. Se mantienen la dirección de escucha y la política de acceso HTTP actuales.

## Funciones del panel

- Periodos rápidos hasta 60 días y rango personalizado, con agrupación por minutos, horas o días. Máximo 1.500 tramos por consulta.
- Filtros comunes por origen SMTP/sendmail, destino local/remoto/sin clasificar y condición de cuenta ignorada.
- Buscador de todos los usuarios registrados, con paginación y selección múltiple de hasta 200 identidades por consulta. El catálogo no depende del intervalo elegido. Se muestran email, nombre UNIX y dominios disponibles; la identidad local interna sigue siendo `local:UID`.
- Gráfica de destinatarios o mensajes aceptados y entregas notificadas. Rechazos en rojo, retenciones en naranja y marcas de bloqueo/desbloqueo. Un clic en un tramo abre ese periodo.
- Dos tablas por usuario, para destinos remotos y locales, y una sección de destinos sin clasificar. Mensajes, destinatarios, entregados, diferidos, fallidos y pendientes. Todas las cabeceras y valores, incluidos números y cronología, se alinean a la izquierda. Las páginas contienen hasta 100 filas entre las categorías; los totales y la gráfica cubren todo el resultado.
- Cronología de los últimos 100 eventos relevantes del periodo. Los bloqueos representan la suspensión registrada por el monitor; los problemas de publicación siguen reflejándose en el estado operativo.
- Refresco cada 60 segundos mientras la pestaña esté visible, sin refresco periódico para rangos históricos fijos. Los cambios de filtros cancelan las consultas anteriores. No hay recursos CDN, cuentas cloud ni dependencias pip/npm en el servidor.

Los estados de las tablas corresponden a los mensajes **aceptados en el intervalo** y su último estado conocido; la gráfica de entregas se sitúa en el instante de la entrega observada. Un mensaje mixto cuenta una vez en cada categoría de destino y una sola vez en el total global de mensajes. Los reintentos no añaden destinatarios ni entregas correctas duplicadas.

Local/remoto sigue la clasificación del monitor y los transportes efectivos de Postfix, incluyendo la distinción de filtros loopback. Usar un relay no cambia por sí solo la categoría. Alias, transportes propios y reinyecciones conservan las limitaciones documentadas en [ARCHITECTURE.md](ARCHITECTURE.md).

## Cobertura y límites de observación

Las cuentas ignoradas participan en estadísticas y continúan excluidas de la protección. El filtro «ignorada» corresponde a la última configuración exportada, no a la condición histórica del día del envío.

Para tráfico monitorizado se aprovecha también la información de destinatarios pendientes ya obtenida del spool. Para cuentas ignoradas, que el analizador no inspecciona, los destinatarios se conocen según sus registros de entrega: hasta entonces se indica sobre incompleto. No se inventan destinos a partir de `nrcpt`.

Se registran rechazos SMTP/cleanup atribuibles a una identidad fiable. Algunos fallos de sendmail/postdrop solo se escriben en stderr y no proporcionan destinatarios en el log: el panel muestra su bloqueo de cuenta, pero no puede contar intentos que Postfix no registra. `sent` remoto significa aceptación por el siguiente servidor, incluido un relay; no prueba entrega en el buzón final. Fin de HOLD no equivale por sí mismo a entrega.

Las horas se muestran en la zona del navegador. Los tramos se alinean en UTC; los extremos del periodo se ajustan a minutos y se muestran explícitamente. El panel indica inicio de captura, última actualización, retraso, sobres incompletos, pérdidas del buzón y restauraciones. Un hueco no debe interpretarse como ausencia demostrada de actividad.

## Funcionamiento y rendimiento

El servicio supervisa un proceso hijo estadístico de prioridad reducida (`runtime.py --analytics-worker` en 2.0), con reintentos de arranque espaciados. No depende de visitas a la web y se detiene con la unidad. La pasada del monitor marca IDs modificados dentro de la transacción de lectura del log; un trabajador copia los cambios mediante SQL nativo y actualiza los agregados. Solo el tráfico omitido por el monitor se interpreta adicionalmente. La confirmación del histórico precede a la retirada de pendientes y comprueba su revisión, para no borrar una modificación posterior.

El trabajador procesa lotes acotados, espera en reposo y usa una caché SQLite pequeña. Las peticiones web consultan el histórico en solo lectura: máximo dos consultas simultáneas, presupuesto SQL de dos segundos y respuestas paginadas. Las gráficas se dibujan con JavaScript/SVG en el navegador.

El presupuesto de aceptación es **hasta un 20 % de CPU adicional sobre carga representativa**, incluyendo el trabajador y consultas. El rendimiento depende del volumen y de la proporción de cuentas que antes se descartaban. La prueba sintética no sustituye una medición en el servidor de pruebas. La memoria del proceso separado es acotada, pero tiene un coste fijo adicional de intérprete; no se afirma un porcentaje constante de RSS para cualquier servidor.

Prueba reproducible del repositorio:

```bash
python3 tools/benchmark_analytics.py --reference v1.3.4 --messages 10000 --repeats 5 --ignored-percent 5 --local-percent 20
```

Compara contra el release estable anterior `v1.3.4`, crea solo datos sintéticos y cuenta captura, procesamiento, comprobación/copia y tres consultas de 60 días. Véase [RELEASE_2.0.0.md](RELEASE_2.0.0.md).

## Integridad y recuperación automática

`analytics.sqlite3` es independiente de `state.sqlite3`. Solo el trabajador escribe en el histórico. Utiliza WAL, `synchronous=FULL`, claves foráneas y transacciones. Las lecturas no tienen permisos SQL de escritura. Los lotes/eventos son idempotentes y pueden repetirse tras una caída entre la confirmación y el reconocimiento.

Se comprueba el histórico al abrirlo y diariamente, con `quick_check` y `foreign_key_check`; se crean copias consistentes mediante la API de SQLite y se conservan las dos últimas. Los borrados por antigüedad se realizan por lotes y no se ejecuta `VACUUM` completo en cada pasada. La caducidad física de una gran acumulación se completa gradualmente; las consultas rechazan periodos anteriores a 60 días.

**Una corrupción del histórico no detiene ni reinicia el analizador antispam.** El trabajador:

1. Cierra su conexión y aísla los archivos estadísticos dañados, incluidos WAL/SHM.
2. Busca la copia más reciente que supere las verificaciones.
3. Restaura esa copia; si ninguna sirve, crea un histórico nuevo.
4. Retoma los cambios pendientes y marca la pérdida de cobertura/restauración.

El código operativo y sus bloqueos, retenciones y cursores no se restauran ni rebobinan por una avería estadística. Puede perderse el histórico confirmado posterior a la copia si ya no está pendiente de exportación. Los dañados se conservan en directorios `analytics-damaged-*` para diagnóstico; retirarlos manualmente después de revisar la incidencia si ocupan espacio.

Si el problema es disco lleno, permisos o un almacenamiento averiado, crear otra base puede fallar también. En ese caso el trabajador queda en reintento y el monitor sigue trabajando en la medida en que **su propio almacenamiento operativo** sea utilizable. Un fallo físico del volumen que contiene también `state.sqlite3` no puede aislarse únicamente con una segunda SQLite en ese mismo volumen.

Diagnóstico:

```bash
sudo journalctl -u outbound-spam.service
sudo cat /var/lib/outbound-spam/analytics-status.json
```

### Quién actualiza el histórico y cuánto tarda

El demonio `outbound-spam.service` lanza y supervisa el módulo estadístico al arrancar, además de lanzar las pasadas del analizador. En 2.0 su proceso se identifica como `runtime.py --analytics-worker DIRECTORIO`. Permanece en segundo plano dentro de la misma unidad. No lo lanza Nginx ni una visita al panel, y no es una tarea cron. Tras cada lote espera aproximadamente 0,5 segundos si había pendientes o 5 segundos si no había trabajo. El analizador espera `run_interval` (60 segundos por defecto) **después de terminar** cada pasada. Un bloqueo exclusivo impide dos trabajadores para el mismo estado.

Para ver los procesos:

```bash
sudo systemctl status outbound-spam.service
pgrep -af 'runtime.py --analytics-worker|/lib/analytics.py'
```

Para ver las duraciones sin abrir SQLite:

```bash
sudo python3 -m json.tool /var/lib/outbound-spam/analytics-status.json
sudo watch -n 1 'python3 -m json.tool /var/lib/outbound-spam/analytics-status.json'
```

Desde 1.5.1, el JSON distingue:

| Campo | Significado |
|---|---|
| `duration_seconds` | Tiempo real del último ciclo completado, incluidos sondeos sin cambios. Puede ser muy pequeño en reposo. |
| `processed_items` | Elementos pendientes procesados en ese ciclo; cero indica sondeo sin pendientes. |
| `last_work_duration_seconds` | Tiempo real del último ciclo exitoso que procesó pendientes, conservado durante el reposo. Incluye el mantenimiento si coincidió con ese ciclo. |
| `last_work_cpu_seconds` | CPU del proceso Python durante ese último ciclo con pendientes; no incluye la CPU de posibles subprocesos Postfix. |
| `last_work_items` | Elementos procesados en ese lote; son mensajes modificados y lotes de eventos/metadatos, no un contador de correos enviados. |
| `last_work_at` | Instante UNIX de finalización de ese lote. |

Estas métricas del último lote se reinician al arrancar el trabajador; son `null` hasta que complete uno con pendientes. Un análisis puede producir varios lotes estadísticos: los tiempos son **por lote**, no la duración total de recuperación de todo un atraso. `ok`, `heartbeat`, `updated_at` y `analysed_until` permiten comprobar además salud y frescura. Las escrituras del estado son atómicas.

En 1.5.0 ya existe `duration_seconds`, pero se sobrescribe con la duración de cada sondeo en reposo. Para medir consumo en esa versión también puede usarse el PID publicado:

```bash
PID=$(sudo python3 -c 'import json; print(json.load(open("/var/lib/outbound-spam/analytics-status.json"))["pid"])')
ps -p "$PID" -o pid,ppid,etime,time,pcpu,pmem,rss,args
```

`etime` es la edad del proceso, `time` la CPU acumulada y `rss` la memoria residente en KiB; ninguno equivale al tiempo real de un lote. No se debe lanzar otro `analytics.py` para cronometrarlo: el servicio ya mantiene un único escritor con su bloqueo.

El buzón de eventos omitidos tiene límite (`analytics_outbox_mb`); si se llena, se registra pérdida de cobertura. Los mensajes operativos pendientes de exportar se conservan para que el trabajador pueda copiarlos antes de su purga ordinaria. Las marcas pendientes también se limitan (65.536 por defecto) y caducan a los 60 días, con aviso de pérdida si hay que descartar cambios: un trabajador averiado no debe impedir indefinidamente la purga del monitor. Conviene supervisar el espacio libre además de la frescura del monitor.

## Volver a 1.3.4

En el servidor de pruebas, disponer del paquete oficial y su checksum, verificarlos y ejecutar **su instalador**, sin restaurar una copia antigua de `state.sqlite3`:

```bash
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf outbound-spam-1.3.4.tar.gz
sudo ./outbound-spam-1.3.4/install.sh --target /root/scripts --config /root/scripts/outbound-spam.conf
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --doctor
sudo systemctl status outbound-spam.service
```

1.3.4 ignora las variables y tablas estadísticas auxiliares, y conserva el esquema operativo 4. Los archivos estadísticos permanecen y deja de ejecutarse el trabajador. `/spam-status` sigue funcionando; `/spam-status/` devuelve 404 al no existir el dashboard en 1.3.4. El instalador conserva el estado activo/parado previo. La prueba de esta entrega recorre ambos saltos con el tar.gz oficial 1.3.4 y un demonio real del candidato.
