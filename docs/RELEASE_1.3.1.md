# outbound-spam 1.3.1

## Cada upgrade deja la configuración completa

Desde esta versión, `--upgrade` añade automáticamente al `.conf` **todas las variables que falten**, tomando como inventario el `.conf.example` del candidato. Se incluyen las variables antiguas que hasta ahora se heredaban solo en memoria, como `monitor_local` y `block_local`.

- Se conservan los valores, comentarios, reglas y asignaciones existentes.
- Los valores predeterminados ausentes se incorporan en un bloque inicial, para que las asignaciones previas del usuario sigan teniendo prioridad después.
- Se manejan escalares, arrays indexados y arrays asociativos, manteniendo sus tipos Bash.
- Se validan sintaxis, presencia de variables y equivalencia de valores efectivos antes de escribir.
- Se guarda respaldo privado, se actualiza la caché y se protege el rollback frente a ediciones externas.
- Repetir `--upgrade` cuando ya está instalada la última versión también completa el `.conf` y sincroniza su ejemplo, sin reiniciar servicios.
- Si el archivo ya está completo, no se duplica ni reescribe.

Este es el contrato para las próximas versiones: el upgrade es responsable de mantener ambos archivos completos.

## Actualizar desde 1.3.0

```bash
cp /root/scripts/outbound-spam.conf /root/scripts/outbound-spam.conf.backup
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
diff -u /root/scripts/outbound-spam.conf.backup /root/scripts/outbound-spam.conf
/root/scripts/outbound-spam.sh --doctor
```

El primer upgrade lo ejecuta todavía el actualizador 1.3.0 y puede mostrar sus campos antiguos, pero el candidato 1.3.1 ya completa el archivo. El diff mostrará el nuevo bloque con las variables ausentes. El actualizador nuevo informa de `config_variables_added` y deja `missing_new_variables=[]`.

La integración de protección PHP/sendmail de 1.3.0 sigue disponible. Si la ACL ya estaba instalada, completar variables no vuelve a instalarla ni requiere otra recarga de Postfix.

## Validación

Focal/Python 3.8 y Noble/Python 3.12: 218 pruebas de integración verificadas, 11 pruebas públicas de configuración y 9 HTTP por entorno. Incluyen upgrades desde releases públicos hasta 1.3.0, el caso con ambas variables locales ausentes y una repetición sin versión nueva. Detalles: [TEST_RESULTS_1.3.1.json](TEST_RESULTS_1.3.1.json).

El esquema SQLite sigue siendo 4 y las API de upgrade/systemd siguen siendo 1. Assets: `outbound-spam-1.3.1.tar.gz`, `antispam-isp-1.3.1.tar.gz` y `SHA256SUMS`.
