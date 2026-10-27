#!/bin/sh
# Toy reset: put the shipped default back after a rotation, so a restored
# host matches a fresh one. Prints RESET_OK, or RESET_NONE with no gate here.
# Logs one line to the container log: signin reset from=<source>
# (or signin reset none from=<source>). $1 carries the source label
# from the exercise tools. TOY_USER_FILE, TOY_PASS_FILE, and TOY_LOG_FILE
# exist only so tests can run this script outside the container.
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
src=$(clean_token "${1:-unknown}")
user_file="${TOY_USER_FILE:-/secret/.toy_user}"
pass_file="${TOY_PASS_FILE:-/secret/.toy_pass}"
if [ ! -f "$pass_file" ]; then
  echo RESET_NONE
  toy_log "signin reset none from=$src"
  exit 0
fi
printf 'dbops' > "$user_file"
printf 'dbops-fake-19' > "$pass_file"
echo RESET_OK
toy_log "signin reset from=$src"
