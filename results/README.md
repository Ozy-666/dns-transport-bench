# Published results

## dnsdoh.art

The run behind the tables and charts in
[Encrypted DNS transports compared](https://dnsdoh.art/guides/doh-vs-dot-vs-dnscrypt-vs-doq.html).

Taken against `dnsdoh.art` (194.180.189.33): one vantage point, one client stack,
20 ms one-way netem delay so a round trip costs 40 ms. `packets.json` and
`pq.json` are medians of 9 cold-connection runs, taken 29 July 2026.
`loss.json` is 300 runs per transport at 2% loss per direction, re-taken on
30 July 2026 after a 60-run version proved too small: at n=60 the 99th
percentile is literally the single worst observation, so the tail figures moved
by more than a second between runs. At n=300 they are stable and both TCP
transports agree.

Headline numbers:

| | time | round trips | bytes | p99 at 2% loss |
|---|---|---|---|---|
| Do53 | 40.8 ms | 1.0 | 217 B | 41.0 ms, but 9/300 never returned |
| DoQ | 105.0 ms | 2.6 | 9 742 B | 310.2 ms |
| DoT | 143.6 ms | 3.6 | 6 833 B | 1 220.2 ms |
| DoH | 144.9 ms | 3.6 | 7 238 B | 1 224.0 ms |
| DoH3 | 147.9 ms | 3.7 | 10 855 B | 329.1 ms, and 13/300 returned no answer |

The loss result worth keeping: both TCP transports sit almost exactly one second
above their own median at the 99th percentile (DoT +1 076 ms, DoH +1 079 ms),
which is the RFC 6298 retransmission timeout floor showing up as a constant
rather than as a distribution. The worst samples, 3 360 ms and 3 343 ms, are one
further doubling of that timer. Neither QUIC transport passed 434 ms.

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
