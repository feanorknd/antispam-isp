# antispam-isp 1.0.0

Primera distribución estable e instalable del control de spam SMTP saliente autenticado.

## Incluye

- Contadores incrementales por cuenta y destinatario externo, incluidos los pendientes de la cola.
- Umbral reducido al cuarto si el dominio remitente SMTP difiere del de la cuenta autenticada.
- Informes exclusivos por cuenta con separación mínima de seis horas.
- Bloqueo remoto por SASL y retención administrable de mensajes, preservando entrega local.
- Servicio systemd con descanso posterior a cada pasada, monitor Monit y configuración editable con nano.
- Copias SQLite verificadas y registro crítico independiente para reconciliar acciones interrumpidas.
- Instalador completo y actualizador `--upgrade` contra los releases públicos de este repositorio.

## Instalación

Descargar **`antispam-isp-1.0.0.tar.gz`** y **`SHA256SUMS`**, comprobar el checksum, extraer y ejecutar como root:

```bash
./install.sh --install-deps
```

Editar `/root/scripts/outbound-spam.conf` antes de activar avisos/acciones, y seguir `docs/INSTALLATION.md` y `docs/UPGRADE.md`. La configuración nueva comienza con avisos desactivados.

## Actualizaciones posteriores

```bash
/root/scripts/outbound-spam.sh --upgrade
```

El archivo operativo y el estado se conservan. El instalador usa versiones coherentes bajo `.antispam/releases/` y un enlace `current` actualizado atómicamente.

## Compatibilidad

Python 3.8+, sin paquetes pip. El comportamiento del detector y de la integración se ha verificado con Postfix 3.4.13, 3.5.25, 3.6.4, 3.8.6 y 3.10.6, en Ubuntu Focal/Jammy/Noble/26.04 y Debian 11.

El paquete y el repositorio publicado excluyen los directorios de pruebas y las copias privadas `etc/` y `var/`. Los resultados de verificación están documentados en `docs/TESTS.md`.
