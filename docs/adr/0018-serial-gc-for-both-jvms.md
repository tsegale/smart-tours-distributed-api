# 18. Serial GC for tour-core and the gateway

Date: 2026-11-05 · Status: Accepted

## Context
Both JVMs ran on whatever the JVM picked from its container limit. Under these limits (768 MiB and 512 MiB) that was
already Serial, but only by ergonomics: a change of limit or CPU count could silently change the collector. The
choice should be explicit and made from measurements.

`benchmarks/run_gc_comparison.py` compared Serial, G1 and generational ZGC under the search mix at a fixed 40 req/s:
16 runs on one laptop, the last 6 (Serial against G1 on tour-core) with GC logs and each measured window timestamped
([results](../benchmarks/jvm.md)).

## Decision
**`-XX:+UseSerialGC` in both Dockerfiles**, next to the existing `-XX:MaxRAMPercentage=75`.

- **tour-core.** Peak RSS: Serial 525 to 567 MiB, G1 626 to 701, ZGC 764 to 777 against a 768 MiB limit. ZGC leaves
  no margin. G1's latency is a little better: in the runs with a quiet host, p95 38.6 to 41.5 ms against Serial's
  43.3, p99 56 to 65 ms against 74. That is real but small next to 100 to 170 MiB more memory, on a stack that shares
  one small VM.
- **Gateway.** Peak RSS is the same on Serial and G1 (275 to 310 and 281 to 294 MiB), and neither showed a latency
  gain that stood out from the noise, so nothing justifies a larger collector. ZGC read 528 MiB against a 512 MiB limit.

## Consequences
- **We give up a few milliseconds of tail latency for memory.** On a host with memory to spare, and for a larger heap,
  that trade reverses: G1 is the usual choice there. Re-run the comparison on the Part B server before sizing it.
- **An unexplained result remains.** In the first 10 runs, which had no GC log, tour-core on Serial logged
  stop-the-world pauses of 210 to 981 ms in 4 of 6 runs, and the 981 ms one came with a p95 of 146 ms while host paging
  was low. The 3 Serial reruns with GC logs had no full collection at all and no pause over 31 ms, so those pauses
  did not reproduce and their cause is not established. A full collection is not ruled out, because Serial stops
  every request for one and none of the logged runs needed one. The search dashboard and SLO alerts (ADR 0017) are what
  would catch this in operation.
- Only 40 req/s of reads was measured. A load that fills the old generation faster would look different, for Serial
  especially.
