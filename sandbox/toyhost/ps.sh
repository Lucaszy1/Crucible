#!/bin/sh
# Toy ps stub: python:slim ships no procps, and blue's list_processes
# only needs an honest sketch of what runs here. Prints the entrypoint
# and this call itself. Takes no arguments.
echo "PID CMD"
echo "1 python /app/toyhost.py"
echo "$$ sh /usr/local/bin/ps"
