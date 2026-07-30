#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Ozy-666 (https://dnsdoh.art)
#
# Remove everything setup.sh created. Safe to run twice.
set -uo pipefail

NS="${NS:-dnsbench}"
HOST_VETH="${HOST_VETH:-veth-b}"
CLIENT_IP="${CLIENT_IP:-10.66.0.2}"
PREFIX="${PREFIX:-30}"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

# netem first: deleting the link would take the qdisc with it, but be explicit.
ip netns exec "$NS" tc qdisc del dev "${NS_VETH:-veth-b-ns}" root 2>/dev/null
tc qdisc del dev "$HOST_VETH" root 2>/dev/null

ip netns del "$NS" 2>/dev/null && echo "removed namespace $NS"
ip link del "$HOST_VETH" 2>/dev/null && echo "removed $HOST_VETH"
rm -rf "/etc/netns/$NS"

if command -v nft >/dev/null 2>&1 && nft list table ip benchnat >/dev/null 2>&1; then
  nft delete table ip benchnat && echo "removed nftables table benchnat"
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -t nat -D POSTROUTING -s "$CLIENT_IP/$PREFIX" -j MASQUERADE 2>/dev/null &&
    echo "removed iptables masquerade rule"
fi

rm -rf /tmp/benchpcap

echo "done. Any firewall rule you added by hand for the veth is still there; remove it too."
