"""Measure independent complete runs and optionally compare each seed to serial."""
from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests" / "benchmark_special_annual_many_seeds.py"
sys.path.insert(0, str(ROOT))

from utils.monte_carlo_ensemble import aggregate_packets


@contextmanager
def keep_system_awake():
    """Hold a system execution request only while this benchmark is active."""
    if sys.platform != "win32":
        yield
        return
    continuous = 0x80000000
    system_required = 0x00000001
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    call = kernel.SetThreadExecutionState
    call.argtypes = [ctypes.c_uint]
    call.restype = ctypes.c_uint
    if not call(continuous | system_required):
        raise ctypes.WinError(ctypes.get_last_error())
    print("system_sleep_request=active", flush=True)
    try:
        yield
    finally:
        if not call(continuous):
            raise ctypes.WinError(ctypes.get_last_error())
        print("system_sleep_request=released", flush=True)


def _launch(start: int, count: int, output: Path, log: Path,
            target_year: int, verify: bool, parity_seeds: str = ""):
    command = [sys.executable, str(WORKER), "--seeds", str(count),
               "--seed-start", str(start), "--target-year", str(target_year),
               "--metric-packets", "--frame-digests", "--packet-output", str(output),
               "--parity-seeds", parity_seeds]
    if not verify:
        command.append("--skip-full-verification")
    command.append("--phase-per-seed")
    stream = log.open("w", encoding="utf-8")
    return subprocess.Popen(command, cwd=ROOT, stdout=stream,
                            stderr=subprocess.STDOUT), stream


def _finish(process, stream, log):
    code = process.wait()
    stream.close()
    if code:
        raise RuntimeError(f"Worker exited {code}; log: {log}\n"
                           + log.read_text(encoding="utf-8", errors="replace")[-4000:])
    content = log.read_text(encoding="utf-8", errors="replace")
    fingerprints = [line.split("=", 1)[1].strip() for line in content.splitlines()
                    if line.startswith("cluster_mapping_sha256_prefix=")]
    if len(fingerprints) != 1:
        raise AssertionError(f"Missing input fingerprint in {log}")
    return fingerprints[0]


def _run_benchmark(args):
    if args.seeds < args.workers or args.workers < 2:
        raise ValueError("--seeds must be at least --workers, and --workers at least 2")
    parity_seeds = {int(value) for value in args.product_parity_seeds.split(",")
                    if value.strip()}
    if any(seed < 0 or seed >= args.seeds for seed in parity_seeds):
        raise ValueError("product parity seeds must be within the measured range")
    with tempfile.TemporaryDirectory(prefix="mc_parallel_") as directory:
        temporary = Path(directory)
        serial = None
        serial_seconds = None
        fingerprints = []
        if not args.skip_serial:
            serial_file = temporary / "serial.json"
            serial_log = temporary / "serial.log"
            started = perf_counter()
            process, stream = _launch(0, args.seeds, serial_file, serial_log,
                                      args.target_year, args.verify_each_seed,
                                      args.product_parity_seeds)
            fingerprints.append(_finish(process, stream, serial_log))
            serial_seconds = perf_counter() - started
            serial = json.loads(serial_file.read_text(encoding="utf-8"))
            print(f"serial_seconds={serial_seconds:.3f}", flush=True)

        parts = []
        start = 0
        for index in range(args.workers):
            count = args.seeds // args.workers + (index < args.seeds % args.workers)
            output = temporary / f"worker_{index}.json"
            log = temporary / f"worker_{index}.log"
            selected = ",".join(str(seed) for seed in sorted(parity_seeds)
                                if start <= seed < start + count)
            parts.append((start, count, output, log, selected))
            start += count
        started = perf_counter()
        running = [(_launch(first, count, output, log, args.target_year,
                            args.verify_each_seed, selected), log)
                   for first, count, output, log, selected in parts]
        for (process, stream), log in running:
            fingerprints.append(_finish(process, stream, log))
        if len(set(fingerprints)) != 1:
            raise AssertionError(f"Input fingerprints differ: {fingerprints}")

        parallel = [item for _, _, output, _, _ in parts
                    for item in json.loads(output.read_text(encoding="utf-8"))]
        parallel.sort(key=lambda item: item["seed"])
        if [item["seed"] for item in parallel] != list(range(args.seeds)):
            raise AssertionError("Parallel seed set is incomplete")
        aggregation_started = perf_counter()
        ensemble = aggregate_packets(
            base_seed=0, count=args.seeds,
            runs=[{"seed": item["seed"], "status": "ok", "packet": item["packet"]}
                  for item in parallel])
        aggregation_seconds = perf_counter() - aggregation_started
        if ensemble["status"] != "complete" or ensemble["successful_count"] != args.seeds:
            raise AssertionError(f"Global ensemble incomplete: {ensemble['failures'][:5]}")
        parallel_seconds = perf_counter() - started
        print(f"parallel_seconds={parallel_seconds:.3f} "
              f"global_aggregation_seconds={aggregation_seconds:.3f}", flush=True)
        if serial is not None:
            serial.sort(key=lambda item: item["seed"])
            if [item["seed"] for item in serial] != list(range(args.seeds)):
                raise AssertionError("Serial seed set is incomplete")
            for original, candidate in zip(serial, parallel):
                if original["packet"] != candidate["packet"]:
                    raise AssertionError(f"Packet differs for seed {original['seed']}")
                if original["frame_digests"] != candidate["frame_digests"]:
                    differing = [key for key in original["frame_digests"]
                                 if original["frame_digests"][key]
                                 != candidate["frame_digests"][key]]
                    raise AssertionError(f"Frames differ for seed {original['seed']}: {differing}")
            print(f"parity=exact seeds={args.seeds}/{args.seeds} "
                  f"speedup={serial_seconds/parallel_seconds:.3f}", flush=True)
        print(f"workers={args.workers} target_year={args.target_year} "
              f"complete={len(parallel)}/{args.seeds} "
              f"input_fingerprint={fingerprints[0]} "
              "checked=packet,product_snapshot,employee,events,annual_observed,"
              "annual_OE_JF_stocks", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-year", type=int, default=2035)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--verify-each-seed", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--skip-serial", action="store_true")
    parser.add_argument("--product-parity-seeds", default="")
    args = parser.parse_args()
    with keep_system_awake():
        _run_benchmark(args)


if __name__ == "__main__":
    main()
