# 1.5.1 · Escala entera y tiempos del trabajador

Actualización de la rama de pruebas `1.5`, compatible con 1.5.0. No cambia el esquema ni los datos de SQLite, el puerto HTTP, las rutas o Nginx.

## Eje Y

La gráfica anterior dividía siempre el máximo en cuatro intervalos y redondeaba cada etiqueta. Con máximo 2 generaba los niveles 0, 0,5, 1, 1,5 y 2, etiquetados como 0, 1, 1, 2 y 2.

La escala nueva elige intervalos enteros de la serie 1/2/5 por potencias de diez, ajustando el límite superior y la posición de las series a esa escala. Con máximo 2 muestra **0, 1, 2**, sin fracciones redondeadas ni etiquetas duplicadas. No modifica los datos consultados ni añade trabajo al servidor.

## Diagnóstico en shell

`analytics-status.json` conserva ahora el tiempo real y de CPU, número de elementos e instante de finalización del último lote con pendientes. Los sondeos sin cambios mantienen esas cifras. `duration_seconds` sigue representando el último ciclo y se añade `processed_items` para distinguir reposo de actividad.

El trabajador sigue siendo un hijo persistente del demonio systemd: funciona sin peticiones al dashboard, espera cinco segundos sin pendientes y medio segundo tras procesar un lote. No se añaden procesos, consultas SQLite ni registros operativos por cada medición; utiliza los relojes monotónico/de CPU del proceso y el archivo de estado existente.

Comandos, interpretación de campos e instalación: [DASHBOARD.md](DASHBOARD.md#quién-actualiza-el-histórico-y-cuánto-tarda).

## Actualizar desde 1.5.0

Copiar el tar.gz 1.5.1 y su `SHA256SUMS` al servidor de pruebas:

```bash
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf outbound-spam-1.5.1.tar.gz
sudo ./outbound-spam-1.5.1/install.sh --target /root/scripts --config /root/scripts/outbound-spam.conf
sudo /root/scripts/outbound-spam.sh --version
sudo systemctl status outbound-spam.service
```

Conserva configuración e histórico y reinicia el servicio si estaba activo. Después recargar la página del dashboard. No es necesario modificar el include de Nginx.

## Validación

- Navegador real: máximos 0, 1, 2, 3, 4, 5, 9, 10, 99, 100 y 10.000, con etiquetas Y enteras y sin duplicados.
- 18 pruebas del histórico correctas, incluida ejecución real del trabajador y conservación de los tiempos tras un sondeo sin pendientes.
- 9 pruebas HTTP y la integración Nginx con autenticación/prefijo correctas.
- Instalación/actualización y retorno comprobados desde 1.3.4 en Focal/Python 3.8 y desde 1.5.0 en Noble/Python 3.12, conservando configuración, estado operativo e histórico. Demonio y trabajador reales dentro de los contenedores; llamadas systemctl controladas por el entorno de pruebas.
