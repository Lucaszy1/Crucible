#!/bin/sh
# Toy reset: put the shipped default back after a rotation, so a restored
# host matches a fresh one. Prints RESET_OK, or RESET_NONE with no gate here.
if [ ! -f /secret/.toy_pass ]; then echo RESET_NONE; exit 0; fi
printf 'admin' > /secret/.toy_user
printf 'changeme' > /secret/.toy_pass
echo RESET_OK
