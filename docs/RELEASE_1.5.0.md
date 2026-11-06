# 1.5.0 · Dashboard e histórico experimental

Rama `1.5`, basada en el release publicado 1.3.4 (`a72f4d4`). `main` mantiene ese release. Paquetes de pruebas separados del canal estable.

## Funcionalidad

- Histórico SQLite de 60 días para SMTP AUTH y sendmail/pickup.
- Cuentas ignoradas incluidas en estadísticas, sin participar en contención; filtros Todas/Excluir/Solo ignoradas.
- Selector múltiple y buscador de usuarios del histórico completo.
- Gráficas SVG dinámicas, periodos y agrupación por minuto/hora/día, tablas local/remoto y cronología de incidencias.
- Mismo puerto HTTP: raíz JSON intacta, panel/API/recursos bajo `/dashboard/`, compatible con el prefijo Nginx `/spam-status/`.
- Proceso estadístico independiente, sincronización incremental por SQL y exportación recuperable de tráfico omitido.
- WAL/FULL, copias verificadas, recuperación automática de corrupción desde backup o histórico nuevo y aviso de cobertura.
- Optimización de las muestras del parser: conservar el array JSON sin volver a decodificar y codificar todas las líneas previas en cada actualización; mantiene un máximo de ocho muestras por mensaje.

## Compatibilidad de instalación

Python 3.8+, SQLite del sistema y navegador con JavaScript. No necesita pip, npm ni CDN. Instalar con `install.sh` del candidato. No cambia el esquema operativo 4 y permite volver mediante el instalador publicado 1.3.4, conservando el estado actual. La validación antigua de `--upgrade` no conoce estos nuevos componentes; no se utiliza para entrar en pruebas.

Instrucciones completas, Nginx, interpretación de métricas y limitaciones: [DASHBOARD.md](DASHBOARD.md).

## Rendimiento

Medición sintética local: 10.000 mensajes, 100 identidades, 60 días, 20 % sendmail y 5 % tráfico ignorado, cinco repeticiones. CPU total mediana 1,422 s frente a 1,231 s de 1.3.4: **+15,50 %**, incluyendo trabajador, mantenimiento/copia y tres consultas de todo el histórico. Pasada principal 1,076 s frente a 1,248 s: **−13,81 %** de tiempo, gracias a la optimización del parser. Pico RSS del proceso de medición 44.996 KiB frente a 41.296 KiB; no representa la suma del RSS de varios procesos en producción. Son datos de carga sintética, no una garantía para toda distribución de tráfico.

La primera implementación que reinterpretaba todo el tráfico se descartó por superar el presupuesto de CPU. El diseño final reutiliza los datos operativos y solo interpreta adicionalmente el tráfico que la protección omite.

## Comprobaciones

La entrega se acompaña de pruebas públicas de deduplicación, transacciones/cursor, revisión de cambios pendientes, reintentos, ID reutilizado, mixtos local/remoto, usuarios ignorados y locales, destinatarios pendientes, filtros, HTTP, respaldo, corrupción con/sin copia y caída del escritor.

```bash
python3 tools/test_analytics.py -v
python3 tools/test_http_status.py -v
python3 tools/test_config_completion.py -v
python3 tools/test_dashboard_proxy.py -v
```

Resultados: 224 pruebas de regresión locales, con 205 correctas y 19 omitidas por requisitos de root/Postfix del entorno; 17 pruebas nuevas del histórico, 9 HTTP, 11 de configuración, 18 de dominios y una integración Nginx correctas. El panel se comprobó también en navegador real, incluyendo filtros dinámicos, usuario de sistema, exclusión de ignoradas, rango de 60 días y ancho móvil de 390 px.

`tools/test_dashboard_proxy.py` usa un Nginx aislado en Docker con autenticación. `tools/test_release150.py` valida el paquete oficial 1.3.4, instalación del candidato, demonio/trabajador, panel y retorno a 1.3.4 en Focal/Python 3.8 y Noble/Python 3.12. Las llamadas systemctl se controlan en la prueba (systemd no es PID 1); el demonio y Postfix se ejecutan de verdad dentro del contenedor.

En Noble se comprobó además corrupción real del histórico con el demonio activo: durante el fallo hubo pasadas correctas del monitor, se mantuvo su PID, el trabajador fue supervisado y recuperó automáticamente la base y los cambios pendientes. Incluye el caso sin copia válida y selección de una copia anterior cuando la última está dañada en las pruebas públicas del histórico.
