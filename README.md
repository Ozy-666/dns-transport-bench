# dns-transport-bench

Measure what encrypted DNS actually costs on the wire: round trips to a first
answer, bytes per cold lookup, behaviour under packet loss, and the price of the
post-quantum key exchange, for **Do53, DoT, DoQ, DoH over HTTP/2 and DoH over
HTTP/3** against a single resolver.

It exists because the usual "DoH vs DoT" comparison is written from the specs.
This one counts packets. Everything runs in a network namespace on a veth pair
with a 1500-byte MTU and a synthetic delay, so the numbers are repeatable and
nothing touches the host's own traffic.

The results it produced for dnsdoh.art are published, with the reasoning, in
[Encrypted DNS transports compared](https://dnsdoh.art/guides/doh-vs-dot-vs-dnscrypt-vs-doq.html).
Raw output from that run is in [`results/`](results/).

---

## What it measures

| Mode | Question it answers |
|---|---|
| `packets` | How many round trips and how many bytes does one cold lookup cost? |
| `loss` | What do the latency percentiles look like when packets go missing? |
| `pq` | What does X25519MLKEM768 add compared to classical X25519? |

A round trip is made visible rather than inferred: with `--delay 20` one round
trip costs exactly 40 ms, so a 105 ms answer is 2.6 round trips.

---

## Requirements

Debian and Ubuntu family. Tested on Debian 12/13 and Ubuntu 22.04/24.04, kernel
5.15 or newer. Root is required throughout, because network namespaces, `tc` and
`tcpdump` all need it.

```bash
sudo apt update
sudo apt install -y python3 iproute2 tcpdump
```

`iproute2` supplies `ip` and `tc`. The `netem` qdisc is a kernel module and is
present in every stock Debian/Ubuntu kernel; on a minimal cloud image you may
need the extra modules package:

```bash
sudo apt install -y linux-modules-extra-$(uname -r)   # only if netem is missing
```

Nothing else from Python: standard library only, no `pip`, no virtualenv.

### The DNS client

Queries are made with [`q`](https://github.com/natesales/q), a Go DNS client
that speaks all five transports with one flag each. That is the point: the
differences you measure are the protocols, not five different client
implementations.

Install a release binary:

```bash
curl -fsSL https://github.com/natesales/q/releases/latest/download/q_linux_amd64.tar.gz \
  | sudo tar -xz -C /usr/local/bin q
q --version
```

Or build it, if you have Go 1.21 or newer:

```bash
go install github.com/natesales/q@latest
sudo install ~/go/bin/q /usr/local/bin/q
```

`q` must be on the `PATH` **inside** the namespace, which it is if it lives in
`/usr/local/bin`. Verify with `sudo ip netns exec dnsbench which q`.

---

## Setup

```bash
git clone https://github.com/Ozy-666/dns-transport-bench.git
cd dns-transport-bench
chmod +x bench.py setup.sh teardown.sh

sudo ./setup.sh
```

`setup.sh` creates a namespace called `dnsbench`, joins it to the host with a
veth pair at MTU 1500, gives the client `10.66.0.2`, adds a masquerade rule so
its packets reach the internet, and pins the resolver's name to its address
inside the namespace so no lookup happens inside a timed measurement.

Point it somewhere else with environment variables:

```bash
sudo SERVER=dns.example.net SERVER_IP=203.0.113.10 ./setup.sh
```

### If the host has a firewall

A restrictive `input` or `prerouting` chain will drop the new source address,
and every transport will simply time out. Allow traffic arriving on the host
side of the veth before benchmarking:

```bash
# nftables, adjust the table and chain names to your own ruleset
sudo nft insert rule inet filter input iifname "veth-b" accept

# iptables
sudo iptables -I INPUT -i veth-b -j ACCEPT
```

Add these at **runtime only**. Do not write them into your persisted firewall
config: they are for the duration of the benchmark, and `teardown.sh` will not
remove them for you.

---

## Verify before you trust anything

```bash
sudo ./bench.py preflight
```

It checks the tools, the namespace, that both ends of the veth are up at MTU
1500, that the resolver's name resolves to the address you gave, that `netem`
attaches, that every transport actually answers, and that a packet capture
returns data. Sample output:

```
[ok ] ip on host  /usr/sbin/ip
[ok ] tc on host  /usr/sbin/tc
[ok ] tcpdump on host  /usr/bin/tcpdump
[ok ] netns dnsbench exists
[ok ] q reachable inside the netns  /usr/local/bin/q
[ok ] veth-b-ns is up
[ok ] veth-b-ns MTU is 1500
[ok ] veth-b MTU is 1500
[ok ] dnsdoh.art resolves inside the netns to 194.180.189.33
[ok ] netem attaches  qdisc netem 8013: root refcnt 65 limit 1000 delay 20ms
[ok ] Do53 answers  40.9 ms (~1.0 round trips)
[ok ] DoT answers  159.1 ms (~4.0 round trips)
[ok ] DoQ answers  149.6 ms (~3.7 round trips)
[ok ] DoH2 answers  143.6 ms (~3.6 round trips)
[ok ] DoH3 answers  149.4 ms (~3.7 round trips)
[ok ] packet capture returns data  {'ms': 40.8, 'pkts_c2s': 1, 'pkts_s2c': 1, ...}

all checks passed, the numbers will mean something
```

A failing transport is usually one of three things: the resolver does not offer
it, the port is blocked on the path, or the host firewall is dropping the veth
source address.

---

## Run

```bash
sudo ./bench.py packets 9      # round trips + bytes    -> results/packets.json
sudo ./bench.py loss 2 60      # 2% loss, 60 samples    -> results/loss.json
sudo ./bench.py pq 5           # post-quantum cost      -> results/pq.json
```

Against another resolver, and a subset of transports:

```bash
sudo ./bench.py --server dns.example.net --ip 203.0.113.10 \
                --only DoT,DoQ packets 9
```

Useful flags: `--delay` (one-way ms, default 20), `--qname`, `--doh-path`,
`--only`, `-o/--out`. `--help` lists them all.

The loss run is the slow one: 60 samples across five transports with timeouts
takes roughly 15 minutes. `netem` is always removed when the run ends, including
on Ctrl-C.

---

## Reading the output

`packets.json`, per transport:

| Field | Meaning |
|---|---|
| `ms` | median time to first answer, cold connection |
| `rtt_est` | that time divided by one round trip: the round-trip count |
| `pkts_c2s` / `pkts_s2c` | packets each way |
| `bytes_c2s` / `bytes_s2c` | IP-layer bytes each way, Ethernet header excluded |
| `flights` | client turns: how many times the client had to speak |

`loss.json` gives `p50` through `p99`, `max`, and `timeouts`, the count of
queries that never returned at all. `pq.json` gives byte totals and timings for
the post-quantum and classical key exchanges plus the `delta` between them.

---

## Teardown

```bash
sudo ./teardown.sh
```

Removes the namespace, the veth, the masquerade rule, `/etc/netns/dnsbench` and
the capture scratch directory. Any firewall rule you added by hand is yours to
remove.

---

## Gotchas that cost real time

- **tcpdump must write a unique file per capture and run with `-U`.** Reuse one
  path and it silently hands back the *previous* transport's capture, so every
  transport reports identical packet counts and the results look plausible. This
  is handled in the code; do not "simplify" it away.
- **Never measure against loopback.** Its MTU is 64 KB, so byte and packet
  counts come out in shapes no real path produces. That is why the harness uses
  a veth pinned to 1500.
- **`q` cannot request X25519MLKEM768 explicitly** (`--tls-curve-preferences`
  rejects the name), but Go offers it first by default. The `pq` mode therefore
  measures the default run against a run with the classical group *forced*.
- **Cold connections only.** Every sample is a fresh process, so there is no
  session resumption and no 0-RTT. A resolver connection that stays open turns
  all of this into a single first lookup.
- **Absolute milliseconds are yours, not universal.** Round-trip counts, byte
  counts and loss behaviour travel between setups. Timings describe the path you
  measured on.

---

## Licence

Code: **BSD 2-Clause**, see [LICENSE](LICENSE).

The measurement data in [`results/`](results/) is published under
**[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**: reuse it, including
commercially, with credit to dnsdoh.art.
