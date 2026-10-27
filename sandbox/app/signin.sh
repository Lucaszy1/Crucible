#!/bin/sh
# Toy sign-in gate for validation exercises. Checks the attempt against the
# live credential files inside this container and prints AUTH_OK or AUTH_FAIL.
# Every attempt appends one line to the container log, never the password:
#   signin ok user=<user> from=<source>
#   signin fail user=<user> from=<source>
# $3 carries the source label from the exercise tools. TOY_USER_FILE,
# TOY_PASS_FILE, and TOY_LOG_FILE exist only so tests can run this
# script outside the container; the live image never sets them.
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
want_user=$(cat "${TOY_USER_FILE:-/secret/.toy_user}" 2>/dev/null)
want_pass=$(cat "${TOY_PASS_FILE:-/secret/.toy_pass}" 2>/dev/null)
user=$(clean_token "${1:--}")
src=$(clean_token "${3:-unknown}")
if [ -n "$1" ] && [ -n "$2" ] && [ "$1" = "$want_user" ] && [ "$2" = "$want_pass" ]; then
  echo AUTH_OK
  toy_log "signin ok user=$user from=$src"
else
  echo AUTH_FAIL
  toy_log "signin fail user=$user from=$src"
fi
