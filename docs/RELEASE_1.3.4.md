# outbound-spam 1.3.4

Ayuda de consola reorganizada para facilitar la lectura y la gestión de alertas.

## Cambios

- Cinco secciones: instalación y desinstalación; aspectos generales; gestión de problemas detectados; customizaciones, usuarios e ignores; casos prácticos al final.
- Separadores de líneas entre secciones y alrededor del título de cada caso práctico numerado.
- Todos los comandos llevan su explicación en la línea inmediatamente inferior; argumentos delimitados como `<CUENTA>`, `<MAXIMO>` y `<SEGUNDOS>`.
- Cinco ejemplos paso a paso: cuenta comprometida, campaña legítima, acciones independientes, web PHP/sendmail y excepción temporal.
- Sin bloque `usage:`. Ejecutar el script sin parámetros muestra la misma ayuda que `--help`, con salida correcta y sin cargar configuración ni ejecutar acciones operativas.
- Color automático solo si la salida es una terminal que anuncia soporte. Salida sin colores al redirigir, sin soporte de terminal o con `NO_COLOR`.
- Documentada y verificada la compatibilidad existente de `--inspect-local-user` con nombre UNIX y UID: `web6` y `5009` consultan la misma identidad si corresponden al mismo usuario.

## Actualizar

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
/root/scripts/outbound-spam.sh --help
```

Versión esperada: **1.3.4**. También se puede consultar la ayuda ejecutando `/root/scripts/outbound-spam.sh` sin parámetros.

Se conservan la configuración, el estado, los incidentes, los bloqueos y las retenciones. Se mantiene la ampliación automática de configuración, Python 3.8+, el esquema SQLite 4 y las API de upgrade/systemd 1.

## Distribución

Assets: `outbound-spam-1.3.4.tar.gz`, `antispam-isp-1.3.4.tar.gz` y `SHA256SUMS`. El segundo paquete conserva la compatibilidad con los actualizadores históricos.

## Validación

- Suite local: 224 pruebas, 205 correctas y 19 omitidas por requisitos del entorno (root, contenedor o servicios nativos); sin fallos.
- Pruebas públicas: 18 de dominios/PSL, 11 de configuración y 9 HTTP, todas correctas.
- Cobertura de ayuda sin argumentos, orden de secciones, separadores de casos prácticos, parámetros delimitados y color con/sin soporte; inspector por nombre UNIX y UID.
- Las pruebas de distribución verifican construcción reproducible, manifiesto, instalación, upgrade, conservación de configuración y rollback con comandos simulados.
