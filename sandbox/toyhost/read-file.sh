#!/bin/sh
# Toy file reader with access logging and session gating. Prints the file
# to stdout like cat, then logs one line to the container log:
#   file read path=<path> bytes=<n> from=<source>
# $1 is the path, $2 the source label from the exercise tools. Gated
# paths (the /secret tree plus the spec's planted files and their parent
# dirs, listed in gated.txt) need the session flag from an earlier
# successful sign-in on this host; without it the read prints
# GATE_REQUIRED and logs nothing, exactly like the Stage 1 simulator.
# Only successful reads log. TOYHOST_STATE and TOY_LOG_FILE exist only
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
path=$1
state="${TOYHOST_STATE:-/run/toyhost}"
src=$(clean_token "${2:-unknown}")
clean_path=$(clean_token "$path")
gated=0
case "$clean_path" in
  /secret|/secret/*) gated=1 ;;
esac
if [ "$gated" = 0 ] && [ -f "$state/gated.txt" ] \
    && grep -qxF "$clean_path" "$state/gated.txt" 2>/dev/null; then
  gated=1
fi
if [ "$gated" = 1 ] && [ ! -f "$state/session" ]; then
  echo GATE_REQUIRED
  exit 1
fi
if [ -z "$path" ] || [ ! -f "$path" ] || [ ! -r "$path" ]; then exit 1; fi
bytes=$(wc -c <"$path" 2>/dev/null | tr -d ' ')
cat "$path" || exit 1
toy_log "file read path=$clean_path bytes=$bytes from=$src"
