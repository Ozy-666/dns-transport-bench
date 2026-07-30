#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Ozy-666 (https://dnsdoh.art)
"""Encrypted-DNS transport benchmark: Do53, DoT, DoQ, DoH/2 and DoH/3.

Runs every query from inside a network namespace joined to the host by a veth
pair with a 1500-byte MTU, so segmentation and packet counts match a real path
rather than loopback's 64 KB frames. netem lives on both ends of that veth, so
the injected delay and loss touch this harness only and never real traffic.

Each sample is a fresh client process, which means a cold connection: no
session resumption and no 0-RTT. See README.md for setup, and run
`bench.py preflight` before trusting any numbers.
"""
import argparse
import json
import os
import re
import statistics as st
import subprocess as sp
import sys
import time

DEFAULTS = {
    "ns": "dnsbench",
    "client_ip": "10.66.0.2",
    "host_veth": "veth-b",
    "ns_veth": "veth-b-ns",
    "qname": "example.com",
    "delay": 20,
    "pcap_dir": "/tmp/benchpcap",
    "doh_path": "/dns-query",
}


class Bench:
    def __init__(self, a):
        self.a = a
        self.transports = {
            "Do53": ["-s", a.ip],
            "DoT": ["-s", f"tls://{a.server}"],
            "DoQ": ["-s", f"quic://{a.server}"],
            "DoH2": ["-s", f"https://{a.server}{a.doh_path}", "--http2"],
            "DoH3": ["-s", f"https://{a.server}{a.doh_path}", "--http3"],
        }
        self.ports = {"Do53": 53, "DoT": 853, "DoQ": 853, "DoH2": 443, "DoH3": 443}
        if a.only:
            keep = [t.strip() for t in a.only.split(",")]
            unknown = [t for t in keep if t not in self.transports]
            if unknown:
                sys.exit(f"unknown transport(s): {', '.join(unknown)}")
            self.transports = {k: v for k, v in self.transports.items() if k in keep}
        # c2s packets are the ones leaving the client address for the server.
        self.c2s_re = re.compile(
            rf"{re.escape(a.client_ip)}\.\d+ > {re.escape(a.ip)}")

    # ---- plumbing ---------------------------------------------------------

    def run(self, cmd, **kw):
        return sp.run(cmd, capture_output=True, text=True, **kw)

    def netns(self, *args):
        return ["ip", "netns", "exec", self.a.ns, *args]

    def set_netem(self, delay_ms=0, loss_pct=0.0):
        """Apply netem to both ends of the veth (client egress and server egress)."""
        for dev, pre in ((self.a.ns_veth, self.netns()), (self.a.host_veth, [])):
            self.run(pre + ["tc", "qdisc", "del", "dev", dev, "root"])
            if delay_ms or loss_pct:
                spec = ["tc", "qdisc", "add", "dev", dev, "root", "netem"]
                if delay_ms:
                    spec += ["delay", f"{delay_ms}ms"]
                if loss_pct:
                    spec += ["loss", f"{loss_pct}%"]
                self.run(pre + spec)

    # ---- measurement ------------------------------------------------------

    def query(self, transport, extra=(), timeout=25):
        """One cold query (fresh process = fresh connection). Returns ms or None."""
        r = self.run(self.netns("q", *self.transports[transport], *extra,
                                self.a.qname, "A", "-S"), timeout=timeout)
        m = re.search(r"in ([\d.]+)(ms|s)\b", r.stdout)
        if not m:
            return None
        v = float(m.group(1))
        return v * 1000 if m.group(2) == "s" else v

    def capture(self, transport, extra=()):
        """One cold query with a packet capture. Returns per-direction counts."""
        port = self.ports[transport]
        os.makedirs(self.a.pcap_dir, exist_ok=True)
        # Unique file per capture and -U (unbuffered): a reused path silently
        # serves the PREVIOUS transport's capture when tcpdump has not flushed,
        # and then every transport reports identical packet counts.
        pcap = f"{self.a.pcap_dir}/{transport}-{time.time_ns()}.pcap"
        tp = sp.Popen(self.netns("tcpdump", "-i", self.a.ns_veth, "-n", "-U",
                                 "-w", pcap, f"port {port}"),
                      stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        time.sleep(0.9)
        ms = self.query(transport, extra)
        time.sleep(0.6)
        tp.terminate()
        try:
            tp.wait(timeout=5)
        except sp.TimeoutExpired:
            tp.kill()
            tp.wait()
        if not os.path.exists(pcap) or os.path.getsize(pcap) < 40:
            return None

        out = self.run(self.netns("tcpdump", "-r", pcap, "-n", "-e", "-tt")).stdout
        os.unlink(pcap)
        pkts = []
        for line in out.splitlines():
            m = re.match(r"([\d.]+) .*length (\d+):", line)
            if not m:
                continue
            frame = int(m.group(2))
            c2s = self.c2s_re.search(line) is not None
            pkts.append(("c2s" if c2s else "s2c", frame - 14))  # strip Ethernet
        if not pkts:
            return None

        flights, prev = 0, None
        for d, _ in pkts:
            if d == "c2s" and prev != "c2s":
                flights += 1
            prev = d
        return {
            "ms": ms,
            "pkts_c2s": sum(1 for p in pkts if p[0] == "c2s"),
            "pkts_s2c": sum(1 for p in pkts if p[0] == "s2c"),
            "bytes_c2s": sum(p[1] for p in pkts if p[0] == "c2s"),
            "bytes_s2c": sum(p[1] for p in pkts if p[0] == "s2c"),
            "flights": flights,
        }

    def warm(self, transport):
        """Prime the resolver cache so its own work is close to zero."""
        for _ in range(2):
            self.query(transport)

    # ---- modes ------------------------------------------------------------

    def preflight(self):
        """Check every prerequisite and report, without measuring anything."""
        ok = True

        def check(label, good, detail="", hint=""):
            nonlocal ok
            ok = ok and good
            line = f"[{'ok ' if good else 'FAIL'}] {label}"
            if detail:
                line += f"  {detail}"
            if hint and not good:
                line += f"\n       {hint}"
            print(line)

        for tool in ("ip", "tc", "tcpdump"):
            p = self.run(["which", tool])
            check(f"{tool} on host", p.returncode == 0, p.stdout.strip())

        p = self.run(["ip", "netns", "list"])
        check(f"netns {self.a.ns} exists", self.a.ns in p.stdout)

        p = self.run(self.netns("which", "q"))
        check("q reachable inside the netns", p.returncode == 0, p.stdout.strip())

        p = self.run(self.netns("ip", "-o", "link", "show", self.a.ns_veth))
        up = "state UP" in p.stdout or "LOWER_UP" in p.stdout
        mtu = re.search(r"mtu (\d+)", p.stdout)
        check(f"{self.a.ns_veth} is up", up)
        check(f"{self.a.ns_veth} MTU is 1500", bool(mtu) and mtu.group(1) == "1500",
              hint=f"got {mtu.group(1) if mtu else 'nothing'}; a larger MTU gives byte "
                   "counts no real path would produce")

        p = self.run(["ip", "-o", "link", "show", self.a.host_veth])
        mtu = re.search(r"mtu (\d+)", p.stdout)
        check(f"{self.a.host_veth} MTU is 1500", bool(mtu) and mtu.group(1) == "1500")

        p = self.run(self.netns("getent", "hosts", self.a.server))
        check(f"{self.a.server} resolves inside the netns to {self.a.ip}",
              self.a.ip in p.stdout, p.stdout.strip())

        self.set_netem(delay_ms=self.a.delay)
        p = self.run(self.netns("tc", "qdisc", "show", "dev", self.a.ns_veth))
        check("netem attaches", "netem" in p.stdout, p.stdout.strip())

        for t in self.transports:
            ms = self.query(t, timeout=15)
            check(f"{t} answers", ms is not None,
                  f"{ms:.1f} ms (~{ms / (self.a.delay * 2):.1f} round trips)" if ms else "",
                  hint="the resolver may not offer it, the port may be blocked, or the "
                       "host firewall is dropping the veth source address")

        c = self.capture(next(iter(self.transports)))
        check("packet capture returns data", c is not None, str(c) if c else "",
              hint=f"tcpdump produced nothing on {self.a.ns_veth}")

        self.set_netem()
        print("\n" + ("all checks passed, the numbers will mean something"
                      if ok else "fix the failures above before running a benchmark"))
        return 0 if ok else 1

    def packets(self, n):
        """Tests 1 + 2: round trips and bytes on the wire, on a clean path."""
        self.set_netem(delay_ms=self.a.delay)
        rtt = self.a.delay * 2
        out = {}
        for t in self.transports:
            self.warm(t)
            runs = [c for c in (self.capture(t) for _ in range(n)) if c]
            if not runs:
                print(f"{t:6} no samples")
                continue
            med = lambda k: st.median([r[k] for r in runs])
            out[t] = {
                "samples": len(runs),
                "ms": round(med("ms"), 1),
                "pkts_c2s": med("pkts_c2s"), "pkts_s2c": med("pkts_s2c"),
                "bytes_c2s": med("bytes_c2s"), "bytes_s2c": med("bytes_s2c"),
                "bytes_total": med("bytes_c2s") + med("bytes_s2c"),
                "flights": med("flights"),
                "rtt_est": round(med("ms") / rtt, 2),
            }
            print(f"{t:6} {out[t]}", flush=True)
        return out

    def loss(self, loss_pct, n):
        """Test 3: latency percentiles with loss in both directions."""
        self.set_netem(delay_ms=self.a.delay, loss_pct=loss_pct)
        out = {}
        for t in self.transports:
            self.warm(t)
            xs = [v for v in (self.query(t) for _ in range(n)) if v is not None]
            if not xs:
                print(f"{t:6} no samples")
                continue
            xs.sort()
            pct = lambda p: round(xs[min(len(xs) - 1, int(len(xs) * p))], 1)
            out[t] = {"n": len(xs), "timeouts": n - len(xs),
                      "p50": pct(.50), "p75": pct(.75), "p90": pct(.90),
                      "p95": pct(.95), "p99": pct(.99), "max": round(xs[-1], 1),
                      "mean": round(st.mean(xs), 1)}
            print(f"{t:6} {out[t]}", flush=True)
        return out

    def pq(self, n):
        """Test 4: cost of the post-quantum key exchange.

        Same client and same transport twice: once with the Go client's default
        groups (X25519MLKEM768 first) and once with the classical group forced.
        """
        self.set_netem(delay_ms=self.a.delay)
        out = {}
        for t in self.transports:
            if t == "Do53":
                continue
            self.warm(t)
            pq = [c for c in (self.capture(t) for _ in range(n)) if c]
            cl = [c for c in (self.capture(t, ["--tls-curve-preferences", "X25519"])
                              for _ in range(n)) if c]
            if not pq or not cl:
                print(f"{t:6} no samples")
                continue
            f = lambda rs, k: st.median([r[k] for r in rs])
            out[t] = {
                "pq_c2s": f(pq, "bytes_c2s"), "pq_s2c": f(pq, "bytes_s2c"),
                "cl_c2s": f(cl, "bytes_c2s"), "cl_s2c": f(cl, "bytes_s2c"),
                "pq_total": f(pq, "bytes_c2s") + f(pq, "bytes_s2c"),
                "cl_total": f(cl, "bytes_c2s") + f(cl, "bytes_s2c"),
                "pq_ms": round(f(pq, "ms"), 1), "cl_ms": round(f(cl, "ms"), 1),
            }
            out[t]["delta"] = out[t]["pq_total"] - out[t]["cl_total"]
            print(f"{t:6} {out[t]}", flush=True)
        return out


def main():
    p = argparse.ArgumentParser(
        description="Benchmark encrypted DNS transports against one resolver.",
        epilog="Run `%(prog)s preflight` first. Needs root (netns, tc, tcpdump).")
    p.add_argument("--server", default="dnsdoh.art",
                   help="resolver hostname, must match its certificate (default: %(default)s)")
    p.add_argument("--ip", default="194.180.189.33",
                   help="resolver IPv4 address, used for Do53 and to tell the two "
                        "directions apart in the capture (default: %(default)s)")
    p.add_argument("--doh-path", default=DEFAULTS["doh_path"],
                   help="DoH endpoint path (default: %(default)s)")
    p.add_argument("--qname", default=DEFAULTS["qname"],
                   help="name to query (default: %(default)s)")
    p.add_argument("--ns", default=DEFAULTS["ns"], help="network namespace (default: %(default)s)")
    p.add_argument("--client-ip", default=DEFAULTS["client_ip"],
                   help="client address inside the namespace (default: %(default)s)")
    p.add_argument("--host-veth", default=DEFAULTS["host_veth"], help="host side of the veth pair")
    p.add_argument("--ns-veth", default=DEFAULTS["ns_veth"], help="namespace side of the veth pair")
    p.add_argument("--delay", type=int, default=DEFAULTS["delay"],
                   help="one-way netem delay in ms; a round trip costs twice this "
                        "(default: %(default)s)")
    p.add_argument("--pcap-dir", default=DEFAULTS["pcap_dir"], help="scratch directory for captures")
    p.add_argument("--only", help="comma-separated subset, e.g. DoQ,DoH3")
    p.add_argument("-o", "--out", help="write results here (default: ./results/<mode>.json)")

    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("preflight", help="check prerequisites, measure nothing")
    sp_pk = sub.add_parser("packets", help="round trips and bytes per cold lookup")
    sp_pk.add_argument("n", nargs="?", type=int, default=9, help="samples (default: %(default)s)")
    sp_ls = sub.add_parser("loss", help="latency percentiles under packet loss")
    sp_ls.add_argument("loss", nargs="?", type=float, default=2.0, help="loss %% per direction")
    sp_ls.add_argument("n", nargs="?", type=int, default=60, help="samples (default: %(default)s)")
    sp_pq = sub.add_parser("pq", help="post-quantum vs classical key exchange")
    sp_pq.add_argument("n", nargs="?", type=int, default=5, help="samples (default: %(default)s)")

    a = p.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root: network namespaces, tc and tcpdump all require it")

    b = Bench(a)
    if a.mode == "preflight":
        sys.exit(b.preflight())

    try:
        if a.mode == "packets":
            out = b.packets(a.n)
        elif a.mode == "loss":
            out = b.loss(a.loss, a.n)
        elif a.mode == "pq":
            out = b.pq(a.n)
    finally:
        b.set_netem()  # always leave the path clean, even on Ctrl-C

    dest = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "results", f"{a.mode}.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        json.dump({"server": a.server, "delay_ms": a.delay, "results": out}, fh, indent=1)
    print("written", dest)


if __name__ == "__main__":
    main()
