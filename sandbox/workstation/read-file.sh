#!/bin/sh
# Toy file reader with access logging. Prints the file to stdout like cat,
# then logs one line to the container log:
#   file read path=<path> bytes=<n> from=<source>
# $1 is the path, $2 the source label from the exercise tools. Only
# successful reads log. Keep in sync with sandbox/db/read-file.sh.
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
src=$(clean_token "${2:-unknown}")
clean_path=$(clean_token "$path")
if [ -z "$path" ] || [ ! -f "$path" ] || [ ! -r "$path" ]; then exit 1; fi
bytes=$(wc -c <"$path" 2>/dev/null | tr -d ' ')
cat "$path" || exit 1
toy_log "file read path=$clean_path bytes=$bytes from=$src"
