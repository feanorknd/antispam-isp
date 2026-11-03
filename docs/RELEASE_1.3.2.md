# outbound-spam 1.3.2

Corrige el fallo `Respuesta web_domain de ISPConfig no válida` provocado por alias válidos de ISPConfig con `system_user=NULL`.

## Corrección

El cliente SQL devuelve el literal `NULL` cuando se consulta `HEX(system_user)` de un campo nulo. Las versiones anteriores intentaban convertirlo desde hexadecimal y abortaban la lectura de dominios.

Ahora se interpreta como ausencia de usuario propio y se resuelve la asociación a través de `parent_domain_id`. Se utiliza el usuario UNIX del registro padre; no se deriva el usuario del número de registro. Los pools con otro usuario explícito mantienen sus dominios separados.

Los errores de formato restantes identifican fila, `domain_id` y campo. No es necesario modificar la base de datos ni sustituir sus NULL.

## Actualizar

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
/root/scripts/outbound-spam.sh --doctor
```

La versión debe ser **1.3.2** y el inventario con los alias afectados debe superar doctor. Para comprobar un remitente de un alias: `--inspect-local-user USUARIO_REAL --sender cuenta@dominio-del-alias.example`.

Se conserva la ampliación automática de todas las variables del `.conf` en cada upgrade, introducida en 1.3.1. El esquema SQLite sigue siendo 4 y las API de upgrade/systemd siguen siendo 1.

## Validación

Las regresiones reproducen los pares alias/padre de los dos servidores afectados, con dominios anonimizados, y contemplan NULL, usuario vacío, alias encadenados, pools independientes y errores contextualizados. Se ha comprobado también la consulta con MariaDB 10.11.19 real y una tabla con los mismos campos nullable; el escenario nativo verifica upgrade desde 1.3.1, doctor, inspector y envío local con Postfix real. Detalles en [TEST_RESULTS_1.3.2.json](TEST_RESULTS_1.3.2.json).

Assets: `outbound-spam-1.3.2.tar.gz`, `antispam-isp-1.3.2.tar.gz` y `SHA256SUMS`.
