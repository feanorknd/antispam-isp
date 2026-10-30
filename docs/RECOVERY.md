# Recuperación del estado SQLite

## Archivos protegidos

El estado operativo sigue en `/var/lib/outbound-spam`, modo 0700. Sus archivos privados son 0600. El subdirectorio `recovery/` contiene:

- Copias SQLite consistentes y verificadas (`backup-*.sqlite3`).
- Dos manifiestos e instantáneas del estado crítico, con checksum SHA-256.
- Un diario encadenado de transacciones críticas, independiente del archivo SQLite principal.
- Directorios `damaged-*` con los originales y el motivo de su retirada.

Las instantáneas se renuevan al abrir una pasada y conservan la generación anterior. El diario añade únicamente las modificaciones críticas, no cada línea de correo. Esto evita reescribir todo el inventario por cada mensaje retenido.

El mapa de integración Postfix es otra SQLite (`outbound-spam-access.sqlite3`), legible por `root:postfix`, modo 0640, y no contiene contraseñas. Los hashes de acciones de contraseña solo pueden aparecer en el estado/registro privado mientras sean necesarios para reconciliar la acción.

## Comprobaciones

- `PRAGMA quick_check` del estado al abrir una operación.
- Validación de metadatos JSON y versión del esquema.
- Punto de control independiente de cursores, para no reanudar una lectura desde un offset alterado y duplicar contadores.
- Comparación del estado crítico con la instantánea y el diario verificados, incluso cuando SQLite siga siendo estructuralmente válido.
- Verificación periódica del mapa de Postfix, según `integrity_interval` (300 s por defecto).
- Copias mediante la API de backup de SQLite, con comprobación de integridad, JSON de evidencias y claves foráneas.

Las copias se generan según `backup_interval` (300 s) y se conservan `backup_copies` (3). No se copia un archivo SQLite en vivo ignorando su WAL.

Un bloqueo de SQLite, un disco lleno o un error de permisos no se interpreta automáticamente como corrupción. En esos casos se preservan los archivos y se informa del error operativo.

## Antes y después de una acción

Antes de publicar un bloqueo, poner HOLD, borrar/liberar un mensaje, enviar un aviso o cambiar una contraseña, se confirma su intención en SQLite y en el registro independiente. Si el registro no puede persistirse, no se autoriza otro efecto externo en esa pasada.

Se conservan, entre otros:

- Cuenta, ID y huella de creación de las retenciones/operaciones de cola.
- Reservas de aviso por cuenta, para mantener las seis horas al restaurar una copia antigua.
- Hash objetivo de un cambio de contraseña pendiente, para comprobar si ya fue aplicado.
- Reconocimientos, supresión de HOLD y resoluciones en curso.

Al reanudar, una operación de cola comprueba su estado real. Si un mensaje liberado ya no está en HOLD, no vuelve a liberarlo. Un ID reutilizado con otra identidad no autoriza eliminar su sustituto.

## Recuperación automática

1. Se cierran las conexiones y se conserva el archivo dañado con sus auxiliares WAL/SHM.
2. Se busca la copia SQLite más reciente que supere las verificaciones; si está dañada, se intenta una anterior.
3. Se restaura el estado crítico más reciente desde su instantánea y diario independiente.
4. Se continúa desde el cursor de la copia restaurada y se reprocesa el tramo posterior del log.
5. Si no queda una copia de los contadores, se reconstruyen desde el log y la cola, conservando el estado crítico verificable y señalando calentamiento de la ventana.

La pérdida del propio archivo SQLite también se contempla si existe historial de recuperación. No se salta silenciosamente al final del log como si fuera una instalación nueva.

Si solo se daña el diario pero SQLite está verificado, el diario puede reconstruirse desde la base. Las copias de los archivos dañados permanecen para diagnóstico.

## Mapa SQLite de Postfix

Se construye un mapa candidato fuera de servicio, se cargan los bloqueos conocidos, se resuelven sus rutas y se valida con SQLite y `postmap`. Solo después se publica. Una consulta fallida no deja visible una tabla vacía que quite las restricciones.

Para cerrar descriptores de SQLite antiguos se reinicia la instancia Postfix. Esa intención de reinicio también queda registrada, de modo que pueda reintentarse si se interrumpe después de publicar el mapa. El reinicio puede interrumpir temporalmente sesiones SMTP; Postfix conserva su cola y reintenta las entregas correspondientes.

Si el mapa conserva bloqueos que no existen en el historial privado y no forman parte de una resolución conocida, no se sobrescriben con una lista incompleta.

## Cuando la información no puede recuperarse

Si se pierden a la vez la base y las referencias críticas verificables, no existe información suficiente para asegurar qué HOLD pertenece al monitor o qué acción ya se ejecutó.

El servicio mantiene el análisis y el error en Monit, conserva las restricciones existentes y suspende los automatismos que podrían repetir avisos, cambiar contraseñas o alterar retenciones sin historial. `--resolve-user`, bloqueo y desbloqueo no declaran una resolución completa en ese estado. La purga manual explícita puede utilizar la identidad SASL actual y la huella verificadas de cada mensaje externo.

No se inventa una base vacía marcada como sana ni se libera correo desconocido. Hay que restaurar una copia verificada o revisar el estado antes de recuperar la automatización completa.

## Diagnóstico y restauración administrativa

```bash
/root/scripts/outbound-spam.sh --status
systemctl status outbound-spam.service
journalctl -u outbound-spam.service
```

Para una restauración administrativa, parar primero el servicio y conservar el directorio completo. Restaurar SQLite junto con su registro crítico correspondiente, manteniendo propiedad/permisos, y volver a iniciar el servicio. No eliminar únicamente `state.sqlite3`: se perdería la relación con retenciones y acciones. Los artefactos de recuperación contienen datos sensibles y se conservan fuera de Git.
