"""Concurrent-session load test for the AI agent service.

This is the actual answer to "will it fall over with many real users" --
a configuration review alone (worker class, worker count, connection
pool) can't tell you that; only sending real concurrent traffic can.

Simulates N simultaneous customers, each opening their own session and
sending a few messages with a small pause between them (matching how a
real person types, not a tight loop), then checks:
  - every request got a timely, non-error response
  - no session cross-contamination: each customer's own session still
    reflects only their own messages afterward, not another concurrent
    customer's

Usage:
    python scripts/concurrent_load_test.py --base-url http://127.0.0.1:3001 \
        --sessions 10 --messages 3

Run this against a real running instance -- local dev to sanity-check the
script itself, then the actual deployed app to answer the real question,
since process/worker behavior differs meaningfully between the two.
"""
from __future__ import annotations

import argparse
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests


@dataclass
class SessionResult:
    session_id: str | None = None
    sent_messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    request_latencies_ms: list[float] = field(default_factory=list)
    cross_contamination_detected: bool = False


def _run_one_simulated_customer(base_url: str, customer_index: int, message_count: int, timeout: float) -> SessionResult:
    result = SessionResult()
    try:
        start = time.monotonic()
        response = requests.post(f"{base_url}/api/session", timeout=timeout)
        result.request_latencies_ms.append((time.monotonic() - start) * 1000)
        response.raise_for_status()
        session_id = response.json()["session"]["id"]
        result.session_id = session_id
    except Exception as exc:  # noqa: BLE001 - reporting every failure mode is the point
        result.errors.append(f"session-create failed: {exc}")
        return result

    marker = f"customer-{customer_index}-marker-{random.randint(10000, 99999)}"
    for turn in range(message_count):
        text = f"{marker} message {turn + 1} of {message_count}"
        try:
            time.sleep(random.uniform(0.2, 0.8))  # a real person pausing to type, not a tight loop
            start = time.monotonic()
            response = requests.post(
                f"{base_url}/api/session/{session_id}/message",
                json={"text": text},
                timeout=timeout,
            )
            result.request_latencies_ms.append((time.monotonic() - start) * 1000)
            response.raise_for_status()
            result.sent_messages.append(text)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"message {turn + 1} failed: {exc}")

    try:
        response = requests.get(f"{base_url}/api/session/{session_id}", timeout=timeout)
        response.raise_for_status()
        session_state = response.json()["session"]
        transcript = " ".join(m.get("text", "") for m in session_state.get("messages", []))
        for sent in result.sent_messages:
            if sent not in transcript:
                result.errors.append(f"sent message missing from own session transcript: {sent!r}")
        if marker.split("-marker-")[0] not in transcript and result.sent_messages:
            result.cross_contamination_detected = True
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"final session read failed: {exc}")

    return result


def run_load_test(base_url: str, session_count: int, message_count: int, timeout: float) -> int:
    print(f"Simulating {session_count} concurrent customers, {message_count} messages each, against {base_url}")
    results: list[SessionResult] = []
    with ThreadPoolExecutor(max_workers=session_count) as pool:
        futures = [
            pool.submit(_run_one_simulated_customer, base_url, i, message_count, timeout)
            for i in range(session_count)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    total_requests = sum(len(r.request_latencies_ms) for r in results)
    all_latencies = [ms for r in results for ms in r.request_latencies_ms]
    total_errors = sum(len(r.errors) for r in results)
    contaminated = [r for r in results if r.cross_contamination_detected]
    failed_sessions = [r for r in results if r.session_id is None]

    print(f"\n{'=' * 60}")
    print(f"Total requests:            {total_requests}")
    print(f"Total errors:              {total_errors}")
    print(f"Sessions that never opened: {len(failed_sessions)}")
    print(f"Cross-contaminated sessions: {len(contaminated)}")
    if all_latencies:
        sorted_latencies = sorted(all_latencies)
        p50 = sorted_latencies[len(sorted_latencies) // 2]
        p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)]
        print(f"Latency p50 / p95 / max (ms): {p50:.0f} / {p95:.0f} / {max(all_latencies):.0f}")
    print(f"{'=' * 60}\n")

    for r in results:
        if r.errors or r.cross_contamination_detected:
            print(f"[session={r.session_id}] errors={r.errors} cross_contamination={r.cross_contamination_detected}")

    ok = total_errors == 0 and not contaminated and not failed_sessions
    print("RESULT: PASS - no dropped requests, no cross-contamination" if ok else "RESULT: FAIL - see details above")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:3001", help="AI agent service base URL")
    parser.add_argument("--sessions", type=int, default=10, help="Number of simulated concurrent customers")
    parser.add_argument("--messages", type=int, default=3, help="Messages per simulated customer")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    args = parser.parse_args()
    raise SystemExit(run_load_test(args.base_url, args.sessions, args.messages, args.timeout))
