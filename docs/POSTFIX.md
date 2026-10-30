# Ajustes de Postfix y recomendaciones

## Compatibilidad verificada

El perfil utiliza mecanismos disponibles desde Postfix 3.1. Se ha probado con los paquetes siguientes, incluidos los cambios de configuración y reinicio de una instancia aislada:

| Sistema | Postfix |
|---|---|
| Ubuntu Focal | 3.4.13 |
| Debian 11 | 3.5.25 |
| Ubuntu Jammy | 3.6.4 |
| Ubuntu Noble | 3.8.6 |
| Ubuntu 26.04 | 3.10.6 |

`postconf -M` permite conocer los transportes realmente definidos; solo se escriben sus variables específicas. Antes del reinicio se ejecuta `postfix check` y se verifican los valores efectivos con `postconf`. Las sobrescrituras incompatibles de `master.cf` se notifican para revisión.

Referencia oficial: [default_transport_rate_delay](https://www.postfix.org/postconf.5.html#default_transport_rate_delay), disponible desde 3.1. Los Postfix anteriores a 3.1 se rechazan antes de modificar archivos; no se sustituye el control por otro de semántica diferente.

## Comandos administrativos

```bash
# Guardar el límite global y el periodo, coordinar main.cf y reiniciar Postfix.
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600

# La misma operación con nombre explícito.
/root/scripts/outbound-spam.sh --set-limits --max-sent 100 --time-period 3600

# Usar los valores ya configurados para instalar/actualizar Ratelimits.
/root/scripts/outbound-spam.sh --configure-postfix

# Creación no interactiva expresamente aceptada.
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600 --yes

# Guardar solo la configuración del analizador.
/root/scripts/outbound-spam.sh --max-sent 100 --time-period 3600 --skip-postfix

# Freno externo explícito en lugar del cálculo automático.
/root/scripts/outbound-spam.sh --configure-postfix --outbound-delay 2s

# Mantener también el retraso por destino, rebajado desde los 30 s anteriores.
/root/scripts/outbound-spam.sh --configure-postfix --destination-delay 10s
```

Si `main.cf` no contiene ajustes previos de este grupo, se pregunta si se crea el bloque y se reinicia Postfix. Responder no conserva los ajustes del analizador, pero no escribe el bloque. Sin terminal, se debe elegir `--yes` o `--skip-postfix`; cron nunca formula preguntas ni edita Postfix al ejecutar `--run`.

Con `--run --max-sent N --time-period S` se aplican valores únicamente a esa ejecución. Para persistirlos y coordinar Postfix, utilizar la operación administrativa sin `--run`.

Se guardan respaldos privados de ambos archivos antes de cambiarlos. Si falla la validación se restauran sin reiniciar; si falla el reinicio se restauran y se intenta arrancar con la configuración anterior. La instancia estándar `/etc/postfix` se reinicia mediante `systemctl restart postfix`. Una ruta de configuración alternativa usa `postfix -c RUTA stop/start`, para no reiniciar una instancia ajena.

## Bloque resultante

Ejemplo para `max_sent=100`, `time_period=3600`, reserva agregada 10 y transportes estándar presentes:

```text
# ================= BEGIN OUTBOUND-SPAM RATELIMITS =================
# Ratelimits
# Detector SASL: 100 destinatarios externos / 3600s; dominio distinto: 25.
# Regla global de salida SMTP: por transporte, independiente del relay/destino.
# Postfix limita entregas; el detector cuenta destinatarios: no son cuotas equivalentes.
default_destination_rate_delay = 10s
default_transport_rate_delay = 0s
smtp_transport_rate_delay = 4s
smtp_destination_rate_delay = 10s
relay_transport_rate_delay = 4s
relay_destination_rate_delay = 10s
# Sin retraso para entregas locales/LMTP/virtual y transportes locales conocidos.
local_destination_rate_delay = 0s
local_transport_rate_delay = 0s
lmtp_destination_rate_delay = 0s
lmtp_transport_rate_delay = 0s
virtual_destination_rate_delay = 0s
virtual_transport_rate_delay = 0s
# Máximos en recepción: por IP; 0 evita limitar también correo local y usuarios NAT.
smtpd_client_message_rate_limit = 0
anvil_rate_time_unit = 60s
# ================== END OUTBOUND-SPAM RATELIMITS ==================
```

Los transportes locales adicionales conocidos, como `dovecot`, `maildrop` o `mailman`, también reciben cero retraso si están definidos. `virtual` es el nombre estándar, no `virt`; una antigua asignación aislada `virt_destination_rate_delay` se retira del bloque gestionado.

### Ambos frenos: destino y transporte

Se conserva el control por destino, reduciendo de 30 a **10 segundos** el valor inicial. Protege el ritmo hacia un destino como Gmail en entrega directa. Con un relay común, el destino que ve Postfix normalmente es ese relay y la capacidad sigue compartida por las cuentas que lo usan. El ritmo posterior del relay hacia Gmail lo determina el propio relay.

El retraso global del transporte y el del destino se aplican simultáneamente: no se suman obligatoriamente en cada entrega. Con 4 s por transporte y 10 s por destino, el mismo destino respeta su pausa de 10 s y otros destinos pueden utilizar turnos disponibles del transporte.

El perfil calcula el freno global del transporte SMTP en modo automático:

```text
segundos = ceil(time_period / (max_sent * postfix_headroom))
segundos = limitar al intervalo 1..5
```

`postfix_headroom` vale 10. Para 100/h, el resultado es 4s: unas 900 solicitudes de entrega SMTP/hora, antes de considerar duración y agrupación de destinatarios. Es un margen agregado de apoyo, **no** una conversión exacta de la cuota por usuario. El máximo automático de cinco segundos evita que una ventana diaria fuerce por sí sola retrasos globales de decenas de segundos.

Puede establecerse `--outbound-delay 0s` o un valor explícito, incluido `30s`. Un freno global comparte capacidad entre usuarios y puede crear cola si se dimensiona por debajo del tráfico legítimo. El aislamiento del infractor lo proporciona la detección por SASL y su bloqueo/HOLD, no un reparto justo por usuario de Postfix.

### Por qué la recepción se propone sin límite por IP

`smtpd_client_message_rate_limit=100` limita solicitudes por IP cliente y afecta también a envíos locales y a usuarios distintos detrás de NAT. No representa 100 destinatarios externos/hora por cuenta.

Para cumplir la exclusión de correo local se propone **0**, mientras el detector aplica los límites únicamente a externos. Se puede elegir expresamente `--postfix-client-limit 100`, pero esa protección volverá a afectar a la recepción SMTP de la IP, incluidos mensajes locales. Otros controles de conexión/autenticación existentes no pertenecen a este bloque.

## Sin relay predeterminado y con relays selectivos

El transporte `smtp` puede entregar directamente al MX, usar `relayhost`, elegir un relay por remitente o recibir un nexthop de `transport_maps`. El freno se aplica al transporte, no al nombre del relay. El ajuste conserva los mapas y decisiones de enrutamiento de ISPConfig.

Si se utilizan varios transportes externos con nombres propios, deben figurar en `external_transports`. Cada transporte tiene su propio freno; la cuota del analizador por cuenta sí suma todos sus destinatarios externos. No se agregan como externos los transportes de filtros locales solo por ejecutar el cliente `smtp`.

Un filtro local que necesite entrega SMTP debe tener un transporte separado del de salida, por ejemplo `amavis`. El perfil reconoce el transporte indicado en `content_filter` y le asigna cero retraso, igual que a los agentes locales/pipe reconocidos. Si comparte exactamente un transporte externo como `smtp`, se notifica la incompatibilidad antes de escribir: no se puede eximir esa etapa dentro de la misma bolsa global. En instalaciones con filtros/reinyección o reglas de localidad dependientes del remitente se deben validar sus rutas específicas.

La prueba nativa usa `relayhost` vacío y comprueba, en la misma instancia:

1. Entrega SMTP directa.
2. Relay seleccionado por `sender_dependent_relayhost_maps`.
3. Relay seleccionado por `transport_maps`.
4. Separación temporal del freno SMTP entre esas entregas.
5. Dos entregas LMTP locales sin esperar ese retraso.
6. Envío local permitido desde una cuenta suspendida al exterior, sin omitir los controles originales.

## Recomendaciones a partir del archivo analizado

| Perfil | Máximo sugerido | Ventana | Observaciones |
|---|---:|---:|---|
| General / cuentas observadas | 100 | 3.600 s | Detecta los tres casos; pasa a 25 con dominio remitente distinto |
| Personal de bajo volumen | 30–50 | 3.600 s | Ajustar por cuenta; no aplicar universalmente a soporte/comercial |
| Buzón de muy bajo volumen, frente a goteo sostenido | 50–100 | 86.400 s | Adecuado si su uso normal son pocos destinatarios/día |
| Automatización o boletín legítimo | Perfil particular medido | Según campaña | Cuenta dedicada y `custom_limits`; dimensionar además el ritmo agregado |

En la muestra, las dos cuentas de envío masivo alcanzan 267/h y 2.265–2.269/24h. La tercera alcanza 106/h, pero solo 197 en su peor ventana de 24 horas. Por eso **no debe sustituirse indiscriminadamente 100/h por 200–300/día**: podría perderse el tercer caso por volumen.

También se observan cuentas fuera de los tres incidentes con picos de 74/h y 185/día, y de 72/h y 135/día. Un máximo general de 50/h produciría alertas adicionales; es preferible un perfil particular para usuarios de poco volumen. Varios buzones de la muestra tienen máximos de solo 4–32 destinatarios/día: una ventana diaria estricta puede ser útil en ellos para detectar un goteo que nunca supere 100/h.

La versión actual admite **una ventana por cuenta**, sustituible mediante `custom_limits`; no aplica simultáneamente una cuota horaria y otra diaria al mismo usuario. Mantener 100/h para las tres cuentas observadas conserva la detección rápida medida.
