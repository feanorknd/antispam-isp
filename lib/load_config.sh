#!/usr/bin/env bash
# Serialización privada: la entrada estándar contiene una instantánea ya validada.
set -euo pipefail
declare -A custom_limits=()
# shellcheck source=../outbound-spam.conf.example
source "$1" >/dev/null
# shellcheck source=/dev/null
source /dev/stdin >/dev/null
for name in log_file state_dir max_sent time_period initial_position max_read_bytes \
    command_timeout stale_after rotation_grace retention_days max_action_age \
    reminder_interval foreign_sender_divisor sample_count notify block_smtp hold_queue reset_password \
    postfix_outbound_delay postfix_destination_delay postfix_headroom postfix_client_message_limit \
    run_interval lock_wait service_unit backup_interval integrity_interval backup_copies \
    routing_mode guard_map postfix_config monit_config monit_enabled monit_script \
    cron_file alert_transport alert_from smtp_host smtp_port smtp_security smtp_user \
    smtp_password ispconfig_database ispconfig_server_id mysql_socket postconf_bin \
    postmap_bin postcat_bin postqueue_bin postsuper_bin postfix_bin sendmail_bin \
    mysql_bin doveadm_bin openssl_bin monit_bin systemctl_bin; do
    printf '%s\0%s\0' "$name" "${!name}"
done
for name in report ignore local_domains external_transports local_transports guard_route_addresses; do
    declare -n values="$name"
    printf '%s\0%s\0' "$name" "${#values[@]}"
    ((${#values[@]} == 0)) || printf '%s\0' "${values[@]}"
done
printf 'custom_limits\0%s\0' "${#custom_limits[@]}"
for name in "${!custom_limits[@]}"; do
    printf '%s\0%s\0' "$name" "${custom_limits[$name]}"
done
