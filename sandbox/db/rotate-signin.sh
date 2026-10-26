#!/bin/sh
# Toy rotation: swap the live default for the fixed rotated value, which the
# tools never reveal. Prints ROTATED_OK, or ROTATE_NONE with no gate here.
if [ ! -f /secret/.toy_pass ]; then echo ROTATE_NONE; exit 0; fi
printf 'r0tated-toy-9' > /secret/.toy_pass
echo ROTATED_OK
