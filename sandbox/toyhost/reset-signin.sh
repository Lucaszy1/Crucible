#!/bin/sh
# Toy reset: put the shipped credentials back after a rotation, so a
# restored host matches a fresh one, and drop the session flag.
# Prints RESET_OK, or RESET_NONE with no gate here. Logs one line to
# the container log:
#   signin reset from=<source>
# (or signin reset none from=<source>). $1 carries the source label
# from the exercise tools. TOYHOST_STATE and TOY_LOG_FILE exist only
# so tests can run this script outside the container.
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
src=$(clean_token "${1:-unknown}")
shipped="$state/passwd.shipped"
if [ ! -f "$shipped" ]; then
  echo RESET_NONE
  toy_log "signin reset none from=$src"
  exit 0
fi
cat "$shipped" > "$state/passwd"
rm -f "$state/session"
echo RESET_OK
toy_log "signin reset from=$src"
