# Outbound Spam Monitor

Control de abuso saliente **SMTP AUTH y webs PHP/sendmail**, con detección incremental, servicio systemd, informes, Monit y contención por identidad. Versión **1.3.2**.

Implementa el **plan A**: el analizador detecta el exceso y después puede bloquear nuevos envíos SMTP, retener la cola de esa cuenta y cambiar su contraseña en ISPConfig. La unidad del límite es el **destinatario externo aceptado**, incluso si el mensaje sigue pendiente. Los reintentos no vuelven a contar.

Para webs, atribuye `pickup uid=...` al usuario UNIX y obtiene automáticamente sus dominios de ISPConfig, incluidos alias/subdominios asociados. El bloqueo local usa un mapa hash de `authorized_submit_users`: no necesita reload por incidente y nunca cambia contraseñas de buzones por un envío PHP. Véase [protección PHP/sendmail y comprobaciones reales](docs/LOCAL_SUBMISSION.md).

## Archivos principales

```text
outbound-spam.sh               Entrada Bash
install.sh                     Instalador para servidor nuevo
outbound-spam.conf.example     Configuración y arrays de ejemplo
lib/outbound_spam.py           Motor Python 3.8+ / SQLite, sin pip
lib/postfix_settings.py        Ajustes coordinados de configuración y Postfix
lib/runtime.py                Configuración estable y servicio systemd
lib/load_config.sh            Lectura de variables y arrays Bash
lib/state_recovery.py         Copias SQLite y registro crítico independiente
lib/distribution.py           Instalación versionada y --upgrade desde GitHub
outbound-spam-status.sh        Comprobador ligero de Monit
docs/                         Documentación completa y seguimiento
VERSION                       Versión de la distribución
```

## Instalación y actualización

Descargar `outbound-spam-1.3.2.tar.gz` y `SHA256SUMS` del [release 1.3.2](https://github.com/feanorknd/antispam-isp/releases/tag/v1.3.2), comprobar el checksum, extraer y ejecutar:

```bash
sudo ./install.sh --install-deps
sudo nano /root/scripts/outbound-spam.conf
sudo /root/scripts/outbound-spam.sh --install-local-guard
sudo /root/scripts/outbound-spam.sh --doctor
```

`--install-deps` instala únicamente `postfix-sqlite`, sin recomendaciones; Python y Postfix deben existir previamente. La configuración nueva empieza con avisos desactivados. La guía indica cómo activar el guard, el servicio y Monit. Para actualizar después:

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
```

El instalador conserva el archivo operativo y el estado. En `/root/scripts/` instala únicamente los dos scripts, la configuración privada y su ejemplo. Código, versiones, respaldos y última configuración válida se reúnen en `/var/lib/outbound-spam/`, con activación atómica mediante `current`. El upgrade migra automáticamente la distribución anterior y reanuda el servicio si estaba activo. Véase [instalación y upgrade](docs/UPGRADE.md).

Al actualizar a 1.3.0 o posterior se instala automáticamente la ACL de inyección local y se ejecuta `postfix check` y `reload` si aún no estaba instalada, conservando la política anterior de `authorized_submit_users`. `monitor_local=true` y `block_local=true` son los valores por defecto. Solo los externos disparan el bloqueo, pero una web bloqueada pierde toda inyección con sendmail/postdrop, también para destinatarios locales.

**Desde 1.3.1, `--upgrade` completa automáticamente todas las variables ausentes del `.conf`**, tomando como inventario el `.conf.example` de la versión instalada. Incluye variables antiguas que solo se heredaban por defecto, escalares y arrays. Conserva las asignaciones y valores del usuario, guarda respaldo privado y actualiza la caché. Si ya está instalada la última versión, también completa el `.conf` y sincroniza el ejemplo sin reiniciar servicios. Repetirlo no duplica variables ni reescribe una configuración completa.

**1.3.2 corrige los alias ISPConfig con `system_user=NULL`**: se resuelven mediante la web padre. Los errores de inventario indican fila, ID y campo. No requiere modificar la base de datos.

1.1.0 añade un registro privado en `/var/log/outbound-spam.log`: hora local y zona del servidor, tipo manual/servicio, estado, incidencias y resultados de las acciones. El instalador configura rotación semanal con ocho copias en `/etc/logrotate.d/outbound-spam`. Detalles y ejemplos en [logs operativos](docs/LOGGING.md).

1.2.0 permite guardar límites y excepciones por cuenta desde el script:

```bash
sudo /root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 2000 900
sudo /root/scripts/outbound-spam.sh --set-ignore boletin@ejemplo.com
```

El límite particular sustituye al anterior y retira la cuenta de ignorados; marcarla como ignorada elimina su límite particular. Se valida y guarda la configuración sin reiniciar servicios, con respaldo y registro operativo. Véase [configuración por cuenta](docs/CONFIGURATION.md#administrar-límites-y-excepciones-desde-el-script).

1.2.1 corrige `Cola no reconocida` al leer sobres Postfix con subdirectorios, como `deferred/A/ABC123DEF0`. Admite rutas planas o con varios niveles, IDs cortos/largos y distintas distribuciones entre colas; conserva las verificaciones de identidad y completa los mensajes pendientes en la siguiente pasada, sin reinicializar el estado.

1.2.2 sirve el estado completo como JSON en `http://127.0.0.1:23456/` desde el propio servicio systemd. La dirección y puerto son configurables. Para NodePing y proxy inverso, véase [SERVICE.md](docs/SERVICE.md#estado-http-y-nodeping). La ejecución operativa requiere el servicio.

## Documentación

Empezar por [docs/README.md](docs/README.md) y [la instalación](docs/INSTALLATION.md).

- [Configuración](docs/CONFIGURATION.md)
- [Webs PHP/sendmail: funcionamiento y batería de comprobaciones](docs/LOCAL_SUBMISSION.md)
- [Operación y resolución de incidentes](docs/OPERATIONS.md)
- [Logs operativos y rotación](docs/LOGGING.md)
- [Diseño y límites del recuento](docs/ARCHITECTURE.md)
- [Pruebas y rendimiento](docs/TESTS.md)
- [Simulación de la semana del 18 al 25 de octubre](docs/SIMULATION_20261025.md)
- [Resultado con dominio remitente y avisos cada seis horas](docs/SIMULATION_0.2.0.md)
- [Ajustes de Postfix, relays selectivos y recomendaciones](docs/POSTFIX.md)
- [Servicio systemd y edición con nano](docs/SERVICE.md)
- [Recuperación de SQLite](docs/RECOVERY.md)
- [Releases e instalación automática](docs/UPGRADE.md)
- [Versiones y compatibilidad](docs/VERSIONS.md)
- [Cambios efectuados](docs/CHANGELOG.md)
- [Tareas, programación y pendientes](docs/TASKS.md)

## Desarrollo

```bash
python3 tools/test_config_completion.py -v
python3 tools/test_websites.py -v
python3 tools/build_release.py --output dist
```

Para reproducir el asset publicado de una versión, construir desde su etiqueta (`v1.0.0`), que fija también la documentación. `main` puede contener anotaciones posteriores a la publicación.

Las pruebas y fixtures permanecen en el entorno local de validación, excluidos de la publicación. Sus resultados y metodología están en [docs/TESTS.md](docs/TESTS.md).

Repositorio público: [feanorknd/antispam-isp](https://github.com/feanorknd/antispam-isp). `.gitignore` excluye `tests/`, `etc/`, `var/`, la configuración operativa, cachés, bases de datos, estados y artefactos de construcción.
