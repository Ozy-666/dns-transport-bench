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
| Do53 | 40.8 ms | 1.0 | 217 B | 41.3 ms, but 12/300 never returned |
| DoQ | 105.0 ms | 2.6 | 9 742 B | 310.3 ms |
| DoT | 143.6 ms | 3.6 | 6 833 B | 1 225.8 ms |
| DoH | 144.9 ms | 3.6 | 7 238 B | 1 222.1 ms |
| DoH3 | 147.9 ms | 3.7 | 10 855 B | 329.4 ms, and 15/300 returned no answer |

The loss result worth keeping: both TCP transports sit almost exactly one second
above their own median at the 99th percentile (DoT +1 082 ms, DoH +1 078 ms),
which is the RFC 6298 retransmission timeout floor showing up as a constant
rather than as a distribution. DoT's worst sample, 3 379 ms, is one further
doubling of that timer. Neither QUIC transport passed 495 ms.

DoH3's 15 no-answer lookups are not client trouble, and they are not a QUIC
design cost either. Chasing them down led to a bug in nginx, filed as
[nginx/nginx#1616](https://github.com/nginx/nginx/issues/1616) with a standalone
reproduction at
[nginx-quic-initial-repro](https://github.com/Ozy-666/nginx-quic-initial-repro).
When the ServerHello spans two QUIC Initial packets and one of them is lost, the
congestion window collapses below the bytes already in flight, and from then on
nginx emits Initial packets carrying nothing but ACKs. The bytes in flight are
Handshake packets the client cannot decrypt until the missing CRYPTO frame
arrives, so the window never reopens and the handshake deadlocks until the client
gives up. Two conditions have to hold together: the ServerHello has to need two
packets, which the 1 088 B ML-KEM key share guarantees, and the certificate flight
has to span several Handshake packets. Forcing the classical key exchange
(`--tls-curve-preferences X25519`) keeps the ServerHello in one packet and gave
100 successes out of 100 under identical loss, which is what first pointed at
handshake size. A two-hunk patch, included in the repro bundle as
`proposed.patch`, gave 0 failures out of 200 here. Until that lands, read DoH3's
tail as "when it answers": the missing 15 are the slowest lookups, so the
percentiles above flatter it.

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
