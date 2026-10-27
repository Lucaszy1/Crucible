#!/bin/sh
# Toy rotation: swap the live default for the fixed rotated value, which the
# tools never reveal. Prints ROTATED_OK, or ROTATE_NONE with no gate here.
# Logs one line to the container log: signin rotated from=<source>
# (or signin rotate none from=<source>). $1 carries the source label
# from the exercise tools. TOY_PASS_FILE and TOY_LOG_FILE exist only
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
src=$(clean_token "${1:-unknown}")
pass_file="${TOY_PASS_FILE:-/secret/.toy_pass}"
if [ ! -f "$pass_file" ]; then
  echo ROTATE_NONE
  toy_log "signin rotate none from=$src"
  exit 0
fi
printf 'r0tated-toy-9' > "$pass_file"
echo ROTATED_OK
toy_log "signin rotated from=$src"
