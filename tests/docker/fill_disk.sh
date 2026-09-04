#!/bin/sh
# Scenario 2 setup: create one large dummy file so "دیسک پره" has an obvious
# culprit for the agent to find via df -> du.
set -e
mkdir -p /var/log/bigapp
dd if=/dev/zero of=/var/log/bigapp/dump.bin bs=1M count=500 2>/dev/null
