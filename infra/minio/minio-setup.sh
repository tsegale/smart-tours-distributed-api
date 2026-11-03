#!/bin/sh
# Runs once per `up` (idempotent): the private tickets bucket and two least-privilege users (ADR 0015).
#   tickets-writer: notification-service, writes and reads ticket PDFs
#   tickets-reader: booking-service, reads only (it presigns downloads; a presigned URL can do no more than its signer)
set -eu
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing local/tickets
# No anonymous access: a new bucket has none, and this makes sure an old one doesn't either.
mc anonymous set none local/tickets
mc admin policy create local tickets-write /setup/tickets-write.json
mc admin policy create local tickets-read /setup/tickets-read.json
mc admin user add local tickets-writer "$TICKETS_WRITER_SECRET_KEY"
mc admin user add local tickets-reader "$TICKETS_READER_SECRET_KEY"
# Attaching an already attached policy is an error in mc; that one case is fine, any other is not.
attach() {
  if ! out=$(mc admin policy attach local "$1" --user "$2" 2>&1); then
    case "$out" in
      *"already"*) echo "policy $1 already attached to $2" ;;
      *) echo "$out" >&2; exit 1 ;;
    esac
  fi
}
attach tickets-write tickets-writer
attach tickets-read tickets-reader
echo "tickets bucket and users ready"
