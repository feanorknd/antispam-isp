# outbound-spam 2.0.0 — Histórico y dashboard

Release estable en `main`, tras la validación de las versiones de pruebas 1.5.0/1.5.1.

## Cambios

- Histórico SQLite independiente de **60 días** para SMTP AUTH y usuarios de sistema/sendmail.
- Gráficas dinámicas con agrupación por minutos, horas o días y selección de periodos.
- Buscador/selección múltiple de usuarios del histórico, filtros local/remoto, origen y cuentas ignoradas. Las cuentas ignoradas siguen excluidas de contención.
- Tablas de envíos locales/remotos, estados y cronología de bloqueos, rechazos y retenciones; todos sus títulos y valores, incluidos números, **alineados a la izquierda**.
- Eje Y entero sin valores repetidos por redondeo. Dibujo en el navegador, sin CDN ni dependencias pip/npm.
- Trabajador único y supervisado, con lotes secuenciales, prioridad reducida y actualización independiente de visitas a la web. Métricas de duración real/CPU y último lote conservadas durante reposo.
- WAL, `synchronous=FULL`, transacciones, copias verificadas y recuperación automática del histórico desde backup o base nueva, sin reiniciar el monitor por una corrupción estadística.
- Raíz de estado HTTP y Monit conservadas. Dashboard, API y recursos en `/dashboard/`, compatibles con Nginx bajo `/spam-status/`.

## Actualizar servidores 1.3.x

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --doctor
sudo systemctl status outbound-spam.service
```

Versión esperada: **2.0.0**. El propio actualizador publicado de **1.3.0, 1.3.1, 1.3.2, 1.3.3 y 1.3.4** admite el paquete, sin pasos intermedios.

La preparación completa todas las variables ausentes del `.conf`, incluidas `analytics_enabled=true` y `analytics_outbox_mb=64`, con respaldo y caché validada. Conserva las asignaciones previas, límites, ignorados, acciones, host/puerto, cursores e inventarios operativos. Una desactivación explícita de estadísticas prevalece. Reanuda únicamente el servicio que estuviera activo; este inicia automáticamente el trabajador y la API. Un servicio parado continúa parado.

Para mantener compatibilidad con la lista cerrada de archivos de 1.3.x, el asset integra los fuentes estadísticos y los recursos web dentro de `lib/runtime.py`, componente ya admitido. Los hashes del bundle se declaran en `RELEASE.json`; los fuentes del repositorio siguen separados. No se extraen módulos sobre el estado ni se instala software adicional. El esquema operativo permanece en **4**, el estadístico en **1**, y las API de actualización/systemd en **1**. Python mínimo: **3.8**.

## Acceso web

El servicio ofrece el panel en `http://127.0.0.1:23456/dashboard/` o el host/puerto configurados. La URL raíz mantiene el estado anterior. Para publicar el panel en cada web se necesita el bloque `location ^~ /spam-status/` descrito en [DASHBOARD.md](DASHBOARD.md#nginx-conservar-spam-status-y-añadir-spam-status). Los servidores que ya lo configuraron durante las pruebas no necesitan cambios; un include con solo la ubicación exacta de estado debe ampliarse una vez. El actualizador no modifica configuraciones Nginx ajenas.

El histórico empieza con la captura nueva; no reconstruye automáticamente los logs anteriores. Los datos recogidos por las pruebas 1.5.x se conservan. El panel informa de retrasos, restauraciones, pérdidas de cobertura y destinatarios incompletos. Una caída del volumen compartido puede afectar al almacenamiento operativo además del histórico; la recuperación estadística no sustituye la disponibilidad del disco.

## Distribución

Assets: `outbound-spam-2.0.0.tar.gz`, `antispam-isp-2.0.0.tar.gz` y `SHA256SUMS`. El segundo conserva el nombre histórico para los actualizadores antiguos. Paquetes reproducibles, sin logs, bases privadas, fotografías ni datos de servidores.

## Validación

Las herramientas públicas incluyen:

```bash
python3 tools/test_release200.py PackedDistributionTests -v
python3 tools/test_analytics.py -v
python3 tools/test_http_status.py -v
python3 tools/test_config_completion.py -v
python3 tools/test_dashboard_proxy.py -v
```

`tools/test_release200.py` en los contenedores Focal/Python 3.8 y Noble/Python 3.12 usa los tar.gz oficiales 1.3.0–1.3.4 y su CLI `--upgrade` real. Solo se sustituye la descarga HTTPS por los mismos assets/metadata locales para ejecutar la matriz sin red; se conservan la validación, preparación, activación y comandos del actualizador publicado. Cubre servicio inicialmente activo/parado, ejecución real del nuevo demonio/trabajador, API y recursos integrados, cuentas ignoradas, destinos mixtos, conservación de configuración/estado, repetición idempotente y estadísticas desactivadas expresamente. Las llamadas systemctl se controlan dentro del contenedor, arrancando el demonio real mediante el adaptador.

La integridad se verifica con pruebas de caída del escritor, repetición de lotes, cambios concurrentes, corrupción con/sin backup y fallback a una copia anterior. Las mediciones sintéticas frente a `v1.3.4` incluyen monitor, trabajador, mantenimiento y consultas; no equivalen a un porcentaje garantizado para cualquier servidor o proporción de tráfico antes ignorado. Instrucciones y límites de observación: [DASHBOARD.md](DASHBOARD.md).

Resultados: 205 regresiones locales correctas, 19 omitidas por requisitos del entorno y cero fallos; 18 de histórico, 9 HTTP, 11 de configuración, 18 de dominios y una integración Nginx correctas. En cada uno de Focal y Noble pasan los ocho métodos públicos de distribución/upgrade, incluidos once escenarios de actualización. También pasa el salto desde 1.5.0 con conservación del histórico y corrupción real durante la ejecución, sin interrupción del analizador y con recuperación automática.

El navegador verificó la alineación izquierda de 23 cabeceras y 139 valores (36 numéricos), usando el runtime integrado del tar.gz. En la carga sintética de 10.000 mensajes y cinco repeticiones, la CPU total mediana aumenta **14,72 %**, dentro del presupuesto acordado del 20 %, y la pasada principal reduce su tiempo **14,44 %** frente a 1.3.4. Estos tiempos no representan una garantía independiente de la carga ni incluyen la suma del RSS de procesos en producción. Registro reproducible: [TEST_RESULTS_2.0.0.json](TEST_RESULTS_2.0.0.json).
