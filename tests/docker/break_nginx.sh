#!/bin/sh
# Scenario 1 setup: introduce a syntax error into nginx.conf and stop nginx,
# so the agent must diagnose "چرا nginx بالا نمیاد؟" from scratch.
set -e
sed -i 's/http {/http {\n    this_is_not_a_directive;/' /etc/nginx/nginx.conf
service nginx stop || true
