# outbound-spam 1.3.0

Protección adicional del spam saliente generado por webs PHP mediante `mail()`/sendmail sin autenticación SMTP.

## Novedades

- Identidad `local:UID` desde el log de pickup; los PID variables no afectan al contador.
- Dominios propios obtenidos automáticamente de ISPConfig, con alias y subdominios asociados, para aplicar la reducción foreign sin listas manuales por web.
- Cuotas de destinatarios externos por usuario local y reglas particulares mediante los comandos existentes.
- Bloqueo local mediante un mapa hash negado en `authorized_submit_users`; publicación atómica sin reload por incidente.
- Informes con identidad web, retención de cola verificada y desbloqueo/resolución por `local:UID`. No se cambian contraseñas de buzones por un incidente local.
- Inspector de UID/usuario y remitente para validar la asociación en el servidor real.

## Actualización

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
postconf authorized_submit_users
/root/scripts/outbound-spam.sh --doctor
```

La versión debe mostrar **1.3.0**. Con los nuevos valores por defecto `monitor_local=true` y `block_local=true`, el upgrade instala automáticamente la ACL, conserva la política previa y ejecuta `postfix check` y `reload`. Los siguientes bloqueos/desbloqueos solo actualizan el mapa. Se migra SQLite a esquema 4, manteniendo las API de distribución y systemd. El servicio se reanuda si estaba activo.

El disparador sigue siendo exclusivamente el exceso de destinatarios externos. **Una web suspendida pierde toda nueva inyección sendmail/postdrop, incluidos destinatarios locales.** La ACL no controla SMTP sin AUTH a localhost ni SMTP directo. La protección periódica tiene una carrera hasta la publicación del bloqueo. Al activar esta cobertura se señala calentamiento; el correo histórico sin pickup leído no se atribuye por cabeceras.

## Comprobaciones reales

```bash
/root/scripts/outbound-spam.sh --inspect-local-user web6
/root/scripts/outbound-spam.sh --inspect-local-user web6 --sender recambios@gremiline.es
/root/scripts/outbound-spam.sh --inspect-local-user web6 --sender prueba@dominio-ajeno.invalid
postmap -s hash:/etc/postfix/outbound-spam-submit-blocked
```

Sustituir el usuario y dominio por los de cada servidor. La [guía PHP/sendmail](LOCAL_SUBMISSION.md) incluye resultados esperados, consulta SQL y prueba funcional con una web dedicada.

## Validación

213 pruebas completas y 9 HTTP correctas en Ubuntu Focal/Python 3.8 y Noble/Python 3.12. Otras 25 pruebas de protección local correctas en Jammy/Postfix 3.6.4. Incluyen upgrade desde el release público 1.2.2, sendmail/postdrop setgid con usuario web real, ACL, HOLD y desbloqueo. La base ISPConfig está simulada; se documentan las comprobaciones de producción. Véase [TEST_RESULTS_1.3.0.json](TEST_RESULTS_1.3.0.json).

Assets: `outbound-spam-1.3.0.tar.gz`, paquete compatible `antispam-isp-1.3.0.tar.gz` y `SHA256SUMS`.
