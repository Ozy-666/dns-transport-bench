#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Ozy-666 (https://dnsdoh.art)
#
# Create the benchmark namespace: a veth pair with a 1500-byte MTU, so packet
# sizes match a real path instead of loopback's 64 KB frames. Nothing here
# touches the host's own routing or DNS.
set -euo pipefail

NS="${NS:-dnsbench}"
HOST_VETH="${HOST_VETH:-veth-b}"
NS_VETH="${NS_VETH:-veth-b-ns}"
HOST_IP="${HOST_IP:-10.66.0.1}"
CLIENT_IP="${CLIENT_IP:-10.66.0.2}"
PREFIX="${PREFIX:-30}"
SERVER="${SERVER:-dnsdoh.art}"
SERVER_IP="${SERVER_IP:-194.180.189.33}"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

if ip netns list | grep -qw "$NS"; then
  echo "namespace $NS already exists; run ./teardown.sh first" >&2
  exit 1
fi

ip netns add "$NS"
ip link add "$HOST_VETH" type veth peer name "$NS_VETH"
ip link set "$NS_VETH" netns "$NS"

ip link set "$HOST_VETH" mtu 1500 up
ip addr add "$HOST_IP/$PREFIX" dev "$HOST_VETH"

ip netns exec "$NS" ip link set lo up
ip netns exec "$NS" ip link set "$NS_VETH" mtu 1500 up
ip netns exec "$NS" ip addr add "$CLIENT_IP/$PREFIX" dev "$NS_VETH"
ip netns exec "$NS" ip route add default via "$HOST_IP"

# Pin the resolver's name to its address inside the namespace. The namespace has
# no working DNS of its own, and we do not want a name lookup inside the timing.
mkdir -p "/etc/netns/$NS"
echo "$SERVER_IP $SERVER" > "/etc/netns/$NS/hosts"
echo "nameserver $SERVER_IP" > "/etc/netns/$NS/resolv.conf"

# Let the client's packets out to the internet.
sysctl -q -w net.ipv4.ip_forward=1
if command -v nft >/dev/null 2>&1; then
  nft list table ip benchnat >/dev/null 2>&1 || nft -f - <<EOF
table ip benchnat {
  chain post { type nat hook postrouting priority srcnat; policy accept;
    ip saddr $CLIENT_IP/$PREFIX masquerade
  }
}
EOF
elif command -v iptables >/dev/null 2>&1; then
  iptables -t nat -C POSTROUTING -s "$CLIENT_IP/$PREFIX" -j MASQUERADE 2>/dev/null ||
    iptables -t nat -A POSTROUTING -s "$CLIENT_IP/$PREFIX" -j MASQUERADE
fi

echo "namespace $NS is up: $CLIENT_IP -> $HOST_IP, $SERVER pinned to $SERVER_IP"
echo
echo "If your host runs a restrictive firewall, its input/prerouting chains will"
echo "drop this new source address. Allow traffic arriving on $HOST_VETH before"
echo "benchmarking, and add the rule at RUNTIME only so it disappears on reload."
echo
echo "Next: sudo ./bench.py preflight"
