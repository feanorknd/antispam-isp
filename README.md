# Outbound Spam Monitor

Control de abuso SMTP **saliente y autenticado**, con detección incremental, servicio systemd, informes, Monit y contención por cuenta. Versión **1.1.0**.

Implementa el **plan A**: el analizador detecta el exceso y después puede bloquear nuevos envíos SMTP, retener la cola de esa cuenta y cambiar su contraseña en ISPConfig. La unidad del límite es el **destinatario externo aceptado**, incluso si el mensaje sigue pendiente. Los reintentos no vuelven a contar.

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

Descargar `outbound-spam-1.1.0.tar.gz` y `SHA256SUMS` del [release 1.1.0](https://github.com/feanorknd/antispam-isp/releases/tag/v1.1.0), comprobar el checksum, extraer y ejecutar:

```bash
sudo ./install.sh --install-deps
sudo nano /root/scripts/outbound-spam.conf
sudo /root/scripts/outbound-spam.sh --doctor
```

`--install-deps` instala únicamente `postfix-sqlite`, sin recomendaciones; Python y Postfix deben existir previamente. La configuración nueva empieza con avisos desactivados. La guía indica cómo activar el guard, el servicio y Monit. Para actualizar después:

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
```

El instalador conserva el archivo operativo y el estado. En `/root/scripts/` instala únicamente los dos scripts, la configuración privada y su ejemplo. Código, versiones, respaldos y última configuración válida se reúnen en `/var/lib/outbound-spam/`, con activación atómica mediante `current`. El upgrade migra automáticamente la distribución anterior y reanuda el servicio si estaba activo. Véase [instalación y upgrade](docs/UPGRADE.md).

Las configuraciones existentes no se reescriben, ni se les añaden valores automáticamente. Las variables nuevas ausentes se notifican para revisión. 1.0.1 corrige la configuración de retrasos en servidores cuyo `master.cf` contiene `discard` y otros agentes específicos de ISPConfig.

1.1.0 añade un registro privado en `/var/log/outbound-spam.log`: hora local y zona del servidor, tipo manual/servicio, estado, incidencias y resultados de las acciones. El instalador configura rotación semanal con ocho copias en `/etc/logrotate.d/outbound-spam`. Detalles y ejemplos en [logs operativos](docs/LOGGING.md).

## Documentación

Empezar por [docs/README.md](docs/README.md) y [la instalación](docs/INSTALLATION.md).

- [Configuración](docs/CONFIGURATION.md)
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
python3 tools/build_release.py --output dist
```

Para reproducir el asset publicado de una versión, construir desde su etiqueta (`v1.0.0`), que fija también la documentación. `main` puede contener anotaciones posteriores a la publicación.

Las pruebas y fixtures permanecen en el entorno local de validación, excluidos de la publicación. Sus resultados y metodología están en [docs/TESTS.md](docs/TESTS.md).

Repositorio público: [feanorknd/antispam-isp](https://github.com/feanorknd/antispam-isp). `.gitignore` excluye `tests/`, `etc/`, `var/`, la configuración operativa, cachés, bases de datos, estados y artefactos de construcción.
