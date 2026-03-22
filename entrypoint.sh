#!/bin/bash
# Force DNS servers (Tailscale exit node may break resolv.conf)
echo "nameserver 8.8.8.8" > /etc/resolv.conf
echo "nameserver 1.1.1.1" >> /etc/resolv.conf

exec "$@"
