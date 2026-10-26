#!/bin/sh
# Toy sign-in gate for validation exercises. Checks the attempt against the
# live credential files inside this container and prints AUTH_OK or AUTH_FAIL.
want_user=$(cat /secret/.toy_user 2>/dev/null)
want_pass=$(cat /secret/.toy_pass 2>/dev/null)
if [ -n "$1" ] && [ -n "$2" ] && [ "$1" = "$want_user" ] && [ "$2" = "$want_pass" ]; then
  echo AUTH_OK
else
  echo AUTH_FAIL
fi
