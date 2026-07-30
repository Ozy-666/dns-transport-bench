# Published results

## dnsdoh.art-2026-07-29

The run behind the tables and charts in
[Encrypted DNS transports compared](https://dnsdoh.art/guides/doh-vs-dot-vs-dnscrypt-vs-doq.html).

Taken on 29 July 2026 against `dnsdoh.art` (194.180.189.33): one vantage point,
one client stack, 20 ms one-way netem delay so a round trip costs 40 ms, medians
of 9 cold-connection runs for `packets` and `pq`, 60 runs at 2% loss per
direction for `loss`.

Headline numbers:

| | time | round trips | bytes | p99 at 2% loss |
|---|---|---|---|---|
| Do53 | 40.8 ms | 1.0 | 217 B | 41 ms, but 2/60 never returned |
| DoQ | 105.0 ms | 2.6 | 9 742 B | 376 ms |
| DoT | 143.6 ms | 3.6 | 6 833 B | 1 205 ms |
| DoH | 144.9 ms | 3.6 | 7 238 B | 1 230 ms |
| DoH3 | 147.9 ms | 3.7 | 10 855 B | 455 ms |

The one that needs explaining: on DoQ the *post-quantum* handshake finished a
full round trip **faster** than the classical one. QUIC forbids a server from
sending more than three times what it has received from an unvalidated address.
A classical ClientHello fits in one 1 308 B packet, so this server may send
3 924 B; it has 4 131 B to send, stops 207 bytes short and must wait. The larger
ML-KEM ClientHello needs two packets, the budget becomes 7 848 B, and the whole
flight goes out at once. The lever is handshake size, not cryptography: a
shorter certificate chain removes the stall, so this result belongs to this
server rather than to the protocol.

Absolute milliseconds describe this path only. Round-trip counts, byte counts
and loss behaviour are the parts that travel.

DNSCrypt is deliberately absent: no DNSCrypt endpoint was run, so there was
nothing to measure.

These files are published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
