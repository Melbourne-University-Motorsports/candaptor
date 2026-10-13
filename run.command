#!/bin/bash
# Finder double-click entry on macOS.
cd "$(dirname "$0")"
bash ./run.sh "$@"
status=$?
if [[ $status -ne 0 ]]; then
  echo
  echo "CANdaptor GUI did not start. Press Return to close."
  read
fi
exit $status
