#!/bin/sh
# Workstation sign-in stub. This host holds no credential store, so every
# attempt fails. It still logs the attempt to the container log:
#   signin fail user=<user> from=<source> note=no-gate
# and prints NO_GATE so the tools keep reporting no sign-in gate here.
# $3 carries the source label from the exercise tools. TOY_LOG_FILE
# exists only so tests can run this script outside the container.
toy_log() {
  if [ -n "$TOY_LOG_FILE" ]; then
    printf '%s\n' "$1" >>"$TOY_LOG_FILE" 2>/dev/null || true
  fi
  if [ -w /proc/1/fd/1 ]; then
    printf '%s\n' "$1" >>/proc/1/fd/1 2>/dev/null || true
  fi
}
clean_token() {
  printf '%s' "$1" | tr '\n\r\011' '___' | tr ' ' '_'
}
user=$(clean_token "${1:--}")
src=$(clean_token "${3:-unknown}")
toy_log "signin fail user=$user from=$src note=no-gate"
echo NO_GATE
