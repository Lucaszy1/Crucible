#!/bin/sh
# Toy directory lister with access logging. Prints names like ls -1,
# then logs one line to the container log:
#   dir list path=<dir> entries=<n> from=<source>
# $1 is the directory, $2 the source label from the exercise tools. Only
# successful listings log. Keep in sync with sandbox/workstation/list-files.sh.
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
src=$(clean_token "${2:-unknown}")
clean_dir=$(clean_token "$dir")
if [ -z "$dir" ] || [ ! -d "$dir" ]; then exit 1; fi
ls -1 "$dir" || exit 1
entries=$(ls -1 "$dir" 2>/dev/null | wc -l | tr -d ' ')
toy_log "dir list path=$clean_dir entries=$entries from=$src"
