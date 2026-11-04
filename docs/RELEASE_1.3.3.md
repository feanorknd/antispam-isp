# outbound-spam 1.3.3

Los envíos PHP/sendmail admiten como propio el **dominio registrable** de los dominios asociados a la web en ISPConfig.

## Cambios

- Si una web tiene `app.gremisat.com` y `repostajes.gremisat.com`, incorpora `gremisat.com` una sola vez. Los remitentes `@gremisat.com` tienen `foreign=false` y conservan el umbral normal.
- Respeta extensiones compuestas: `app.empresa.com.es` incorpora `empresa.com.es`; `app.empresa.co.uk` incorpora `empresa.co.uk`.
- Public Suffix List incluida (ICANN y PRIVATE), con reglas exactas, comodines, excepciones e IDNA. No necesita red ni dependencias pip durante el análisis.
- Conserva los dominios originales. El padre añadido no autoriza automáticamente subdominios hermanos ni sufijos públicos, y se mantienen las identidades/cuotas independientes por UID.

La instantánea PSL distribuida es `2026-10-31_14-17-28_UTC`, con 10.396 reglas y licencia MPL-2.0. Procedencia y hash en `docs/PUBLIC_SUFFIX_LIST.json`.

## Actualizar

```bash
/root/scripts/outbound-spam.sh --upgrade
/root/scripts/outbound-spam.sh --version
/root/scripts/outbound-spam.sh --inspect-local-user web6 --sender correo@gremisat.com
```

Versión esperada: **1.3.3**. En el ejemplo, `domains` debe contener ambos subdominios y `gremisat.com`, con `foreign=false`.

Los mensajes ya clasificados conservan su instantánea histórica; los nuevos utilizan la regla ampliada. Los incidentes y bloqueos existentes se mantienen. Se conserva la configuración y su ampliación automática, el esquema SQLite 4 y las API de upgrade/systemd 1.

## Validación

Focal/Python 3.8 y Noble/Python 3.12: **221 pruebas de integración + 18 de dominios/PSL + 11 de configuración + 9 HTTP por entorno**, sin errores ni omisiones. Incluye upgrade desde 1.3.2 y envío real de sendmail con remitente del dominio registrable. Los actualizadores 1.0.2, 1.3.0, 1.3.1 y 1.3.2 validan ambos formatos de paquete y ejecutan la corrección desde el candidato extraído.

Pruebas públicas: `python3 tools/test_websites.py -v`. Incluyen el caso de dos subdominios con el mismo padre, `com.es`, `co.uk`, sufijos privados, comodines/excepciones PSL, IDNA y separación de dominios ajenos. Resultados de integración y actualización: [TEST_RESULTS_1.3.3.json](TEST_RESULTS_1.3.3.json).

Assets: `outbound-spam-1.3.3.tar.gz`, `antispam-isp-1.3.3.tar.gz` y `SHA256SUMS`.
