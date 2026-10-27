#!/bin/sh
# Toy directory lister with access logging and session gating. Prints
# names like ls -1, then logs one line to the container log:
#   dir list path=<dir> entries=<n> from=<source>
# $1 is the directory, $2 the source label from the exercise tools.
# Gated dirs (the /secret tree plus the spec's planted parent dirs in
# gated.txt) need the session flag from an earlier successful sign-in;
# without it the listing prints GATE_REQUIRED and logs nothing,
# exactly like the Stage 1 simulator. Only successful listings log.
# TOYHOST_STATE and TOY_LOG_FILE exist only so tests can run this
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
dir=$1
state="${TOYHOST_STATE:-/run/toyhost}"
src=$(clean_token "${2:-unknown}")
clean_dir=$(clean_token "$dir")
gated=0
case "$clean_dir" in
  /secret|/secret/*) gated=1 ;;
esac
if [ "$gated" = 0 ] && [ -f "$state/gated.txt" ] \
    && grep -qxF "$clean_dir" "$state/gated.txt" 2>/dev/null; then
  gated=1
fi
if [ "$gated" = 1 ] && [ ! -f "$state/session" ]; then
  echo GATE_REQUIRED
  exit 1
fi
if [ -z "$dir" ] || [ ! -d "$dir" ]; then exit 1; fi
ls -1 "$dir" || exit 1
entries=$(ls -1 "$dir" 2>/dev/null | wc -l | tr -d ' ')
toy_log "dir list path=$clean_dir entries=$entries from=$src"
