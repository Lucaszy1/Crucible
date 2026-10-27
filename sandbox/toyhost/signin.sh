#!/bin/sh
# Toy sign-in gate for generated-scenario hosts. Checks the attempt against
# the live credential file the entrypoint wrote and prints AUTH_OK or
# AUTH_FAIL. Workstation-role hosts hold no gate: every attempt fails and
# prints NO_GATE. Every attempt appends one line to the container log,
# never the password:
#   signin ok user=<user> from=<source>
#   signin fail user=<user> from=<source>
#   signin fail user=<user> from=<source> note=no-gate
# A success touches the session flag, which gates file reads. $3 carries
# the source label from the exercise tools. TOYHOST_STATE, TOY_USER_FILE,
# TOY_PASS_FILE, and TOY_LOG_FILE exist only so tests can run this
# script outside the container.
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
state="${TOYHOST_STATE:-/run/toyhost}"
role=$(cat "$state/role" 2>/dev/null)
user=$(clean_token "${1:--}")
src=$(clean_token "${3:-unknown}")
if [ "$role" = "workstation" ]; then
  toy_log "signin fail user=$user from=$src note=no-gate"
  echo NO_GATE
  exit 0
fi
pass_file="$state/passwd"
if [ -n "$TOY_USER_FILE" ] && [ -n "$TOY_PASS_FILE" ]; then
  want_user=$(cat "$TOY_USER_FILE" 2>/dev/null)
  want_pass=$(cat "$TOY_PASS_FILE" 2>/dev/null)
else
  want_user="$1"
  want_pass=$(awk -F: -v u="$1" '$1==u{print $2}' "$pass_file" 2>/dev/null)
fi
if [ -n "$1" ] && [ -n "$2" ] && [ "$1" = "$want_user" ] && [ -n "$want_pass" ] && [ "$2" = "$want_pass" ]; then
  echo AUTH_OK
  toy_log "signin ok user=$user from=$src"
  touch "$state/session" 2>/dev/null || true
else
  echo AUTH_FAIL
  toy_log "signin fail user=$user from=$src"
fi
