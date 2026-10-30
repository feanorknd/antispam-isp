# antispam-isp 1.0.1

Corrección del error `No se verifica discard_destination_rate_delay: esperado '0s', obtenido ''` al configurar Postfix.

## Cambios

- Se comprueba con el Postfix instalado qué variables de retraso están expuestas antes de generar el bloque.
- Se omiten las claves locales no reconocidas, como las de `discard`, sin depender de un `master.cf` fijo.
- Se conservan los parámetros admitidos de entrega SMTP, relay, LMTP y agentes locales conocidos.
- Se soportan nombres con guion y no se confunde `smtp/inet` con el transporte de salida.
- Los mapas de relay/MX/FILTER y el resto de reglas ajenas a Ratelimits se preservan.
- Instalación y upgrade conservan el archivo privado de configuración, sin añadir ni alterar valores. Las variables nuevas ausentes solo se notifican.

## Actualizar desde 1.0.0

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
```

Después repetir la operación que había fallado:

```bash
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600 --destination-delay 10s --outbound-delay 4s
```

El upgrade no ejecuta ese ajuste automáticamente: solo el comando administrativo explícito cambia los límites/configuración de Postfix. La operación fallida anterior restaura los archivos antes de ordenar un reinicio.

El paquete contiene únicamente `postfix-sqlite` como dependencia solicitada por `--install-deps`; Python y Postfix deben existir previamente. No cambia el esquema SQLite (3).
