# outbound-spam 1.2.1

Parche del análisis de colas Postfix: corrige el error **`Cola no reconocida`** cuando los sobres se encuentran en subdirectorios de hash.

## Corrección

El lector de `postcat` admite tanto `cola/ID` como `cola/H/.../ID`, sin una profundidad fija. Reconoce `incoming`, `active`, `deferred` y `hold` con IDs cortos y largos, y permite que cada cola tenga una disposición diferente.

Por ejemplo, interpreta correctamente:

```text
*** ENVELOPE RECORDS deferred/ABC123DEF0 ***
*** ENVELOPE RECORDS deferred/A/ABC123DEF0 ***
*** ENVELOPE RECORDS deferred/A/B/ABC123DEF0 ***
```

Se comprueba toda la cabecera y el ID exacto solicitado. Se mantienen las verificaciones SASL, la huella del mensaje y las protecciones frente a IDs reutilizados o retenciones ajenas.

## Actualización

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --status
```

La versión debe mostrar **1.2.1**. El servicio se reanuda si estaba activo y en su siguiente pasada vuelve a completar los mensajes pendientes de análisis. Se conservan configuración, SQLite, cursores, recuperación, incidentes, bloqueos y retenciones. No es necesario cambiar los parámetros de hash de Postfix ni reinicializar el estado. El calentamiento sigue siendo informativo e independiente de los errores de cola.

## Verificación

- **185 pruebas correctas** en cada una de las cinco distribuciones: Ubuntu Focal/Jammy/Noble, Debian 11 y Ubuntu 26.04, con Postfix **3.4.13, 3.6.4, 3.8.6, 3.5.25 y 3.10.6**, respectivamente.
- Pruebas de lectura con cero, uno y varios niveles de hash e IDs cortos/largos; rechazo de IDs distintos, prefijos parciales, secciones incorrectas y rutas no válidas.
- Postfix real: análisis desde el CLI, HOLD, liberación, reencolado, borrado y purga, con colas planas, con uno/dos/tres niveles y mixtas, e IDs cortos/largos en cada disposición.
- Recuperación del análisis de mensajes ya registrados como incompletos, sin reset ni doble cómputo.
- Upgrade con el CLI/actualizador publicado de **1.2.0**, unidad activa/parada y configuración conservada. Se mantienen las regresiones desde 1.1.0 y 1.0.x.

Las llamadas a systemctl se simulan en los contenedores; el demonio, Postfix, los validadores Monit/systemd y logrotate se ejecutan realmente. Se conservan Python 3.8+, esquema SQLite 3 y `upgrade_api=1`.

Assets: **`outbound-spam-1.2.1.tar.gz`**, paquete histórico compatible **`antispam-isp-1.2.1.tar.gz`** y **`SHA256SUMS`**.
