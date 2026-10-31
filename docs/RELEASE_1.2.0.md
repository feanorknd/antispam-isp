# outbound-spam 1.2.0

## Límites y excepciones desde el script

Dos nuevos comandos guardan las reglas por cuenta directamente en `outbound-spam.conf`:

```bash
sudo /root/scripts/outbound-spam.sh --set-custom-limit boletin@ejemplo.com 2000 900
sudo /root/scripts/outbound-spam.sh --set-ignore boletin@ejemplo.com
```

- **`--set-custom-limit CUENTA MAXIMO SEGUNDOS`** establece el máximo de destinatarios externos en la ventana indicada, sustituye el límite anterior y retira la cuenta de ignorados. El ejemplo permite 2.000 destinatarios externos en 900 segundos; se mantiene la reducción por dominio remitente distinto.
- **`--set-ignore CUENTA`** añade la cuenta a ignorados y elimina su límite particular.
- Repetir una regla idéntica no crea duplicados ni reescribe la configuración. Las cuentas se normalizan a minúsculas y las demás reglas se conservan.

Se comprueban la dirección, los argumentos, los enteros positivos y la retención necesaria para el periodo. Se valida la configuración resultante antes de guardarla, se conserva un respaldo privado y se actualiza la última copia válida. Las operaciones se coordinan mediante el bloqueo operativo y releen la configuración para evitar perder cambios concurrentes.

La configuración se guarda en un único bloque final `OUTBOUND-SPAM ACCOUNT RULES`, que prevalece sobre las declaraciones anteriores. Para editar después las listas a mano, modificar ese bloque. Los resultados se registran en `/var/log/outbound-spam.log`.

Los cambios se aplican en la siguiente pasada, sin reiniciar servicios. Los bloqueos e incidentes existentes y el correo ya retenido mantienen su resolución administrativa independiente; para resolver un falso positivo, ajustar la regla y usar después `--resolve-user CUENTA`.

`--help` incluye ambos comandos y ejemplos prácticos de asignación, sustitución y cambio entre límite e ignorado.

## Actualizar

```bash
sudo /root/scripts/outbound-spam.sh --upgrade
sudo /root/scripts/outbound-spam.sh --version
sudo /root/scripts/outbound-spam.sh --help
```

Se conservan configuración, estado, rutas privadas y política de logs de 1.1.0. El upgrade no añade reglas automáticamente; el bloque gestionado se crea al utilizar los nuevos comandos. Se mantienen Python 3.8+, SQLite 3 y `upgrade_api=1`.

Paquete principal: **`outbound-spam-1.2.0.tar.gz`**. También se publica `antispam-isp-1.2.0.tar.gz` para los actualizadores 1.0.x, junto con `SHA256SUMS`.

## Verificación

**176 pruebas correctas por distribución** en Ubuntu Focal, Jammy, Noble, Debian 11 y Ubuntu 26.04. Incluyen argumentos inválidos, prioridades, guardado/rollback, dos procesos concurrentes y upgrade con los actualizadores públicos 1.0.2 y 1.1.0, seguido del uso de ambos comandos nuevos. Postfix, validadores Monit/systemd, demonio y logrotate se ejecutan realmente en contenedores; las llamadas a systemctl se simulan. Detalles en [TESTS.md](TESTS.md).
