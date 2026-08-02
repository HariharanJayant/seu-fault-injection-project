#!/usr/bin/env python3
"""
run_campaign.py -- orchestrates a full fault-injection campaign.

For each trial, this script:
  1. Launches a fresh QEMU instance running the firmware, paused at reset
     (`-s -S`: GDB stub enabled, CPU halted).
  2. Randomly picks a fault: WHEN (how many instructions to let it run
     before injecting), WHAT (a CPU register or a RAM address), and WHICH
     bit to flip.
  3. Runs gdb (via inject_one.py) to perform the step-then-flip-then-
     continue sequence.
  4. Waits for QEMU to exit (or times out -> "HANG"), reads its exit code,
     and classifies the outcome:
        exit code 0   -> PASS       (fault had no effect on the final result)
        exit code 1   -> FAIL / SDC (fault silently corrupted the output --
                                      the checksum caught it)
        timeout       -> HANG       (fault sent the CPU into an infinite
                                      loop / unreachable state)
        other/signal  -> CRASH      (QEMU itself terminated abnormally,
                                      e.g. an unhandled trap)
  5. Appends one row per trial to a CSV file for later statistical analysis.

Usage:
    python3 run_campaign.py --trials 500 --output results.csv

Each trial is fully independent (a fresh QEMU process), so trials can be
re-run, parallelized later, or resumed by concatenating CSVs -- there is
no shared state between trials other than the random seed.
"""

import argparse
import csv
import os
import random
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_DIR = os.path.join(HERE, "..", "firmware")
FIRMWARE_ELF = os.path.join(FIRMWARE_DIR, "firmware.elf")
INJECT_SCRIPT = os.path.join(HERE, "inject_one.py")

# Total instructions the *unfaulted* firmware executes before it exits
# (measured empirically -- see tools/count_instructions.sh). We sample
# injection points across this range so faults land at every phase of
# the workload: matrix multiply, CRC loop, and the final compare/exit.
DEFAULT_MAX_INSTR = 2800

# General-purpose RISC-V integer registers, by ABI name. x0 ("zero") is
# excluded from the main sweep since it is hardwired and any write to it
# is architecturally a no-op -- not a meaningful fault -- though you can
# add it back as a negative-control sanity check if you like.
GPRS = (
    ["ra", "sp", "gp", "tp"]
    + [f"t{i}" for i in range(0, 3)]
    + [f"s{i}" for i in range(0, 2)]
    + [f"a{i}" for i in range(0, 8)]
    + [f"s{i}" for i in range(2, 12)]
    + [f"t{i}" for i in range(3, 7)]
)

# Address window covering the firmware's code + rodata + data (see
# firmware/firmware.dis after building). Faults here can land on an
# instruction encoding (code corruption) or on the matrix/vector data
# (data corruption) -- both are realistic SEU targets in a real chip.
MEM_LOW = 0x80000000
MEM_HIGH = 0x80000300  # exclusive; word-aligned addresses only

QEMU_CMD = [
    "qemu-system-riscv64", "-M", "virt", "-nographic", "-bios", "none",
    "-kernel", FIRMWARE_ELF, "-smp", "1", "-m", "64M", "-s", "-S",
]

GDB_TIMEOUT_SEC = 8      # wall-clock budget for gdb to step+flip+continue
QEMU_EXIT_TIMEOUT_SEC = 3  # extra grace period for qemu to exit after gdb detaches


def pick_fault(rng, max_instr):
    skip = rng.randint(5, max_instr - 5)
    if rng.random() < 0.5:
        target = "register"
        reg = rng.choice(GPRS)
        addr = ""
    else:
        target = "memory"
        reg = ""
        addr = hex(rng.randrange(MEM_LOW, MEM_HIGH, 4))
    bit = rng.randrange(0, 32)
    return skip, target, reg, addr, bit


def run_one_trial(trial_id, rng, max_instr, gdb_port):
    skip, target, reg, addr, bit = pick_fault(rng, max_instr)

    result_file = f"/tmp/inject_result_{gdb_port}.txt"
    if os.path.exists(result_file):
        os.remove(result_file)

    # Base command minus the trailing "-s -S" (last 2 args), replaced with
    # an explicit per-trial port via "-gdb tcp::PORT -S" so trials could
    # later be parallelized without port collisions.
    qemu_cmd = QEMU_CMD[:-2] + ["-gdb", f"tcp::{gdb_port}", "-S"]
    qemu_proc = subprocess.Popen(
        qemu_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)  # give QEMU a moment to open the GDB socket

    env = os.environ.copy()
    env.update({
        "INJECT_GDB_PORT": str(gdb_port),
        "INJECT_SKIP_INSTRUCTIONS": str(skip),
        "INJECT_FAULT_TARGET": target,
        "INJECT_FAULT_REG": reg,
        "INJECT_FAULT_ADDR": addr,
        "INJECT_FAULT_BIT": str(bit),
        "INJECT_RESULT_FILE": result_file,
    })

    gdb_status, gdb_detail = "GDB_TIMEOUT", ""
    try:
        subprocess.run(
            ["gdb-multiarch", "-batch", "-x", INJECT_SCRIPT, FIRMWARE_ELF],
            env=env, timeout=GDB_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(result_file):
            with open(result_file) as f:
                line = f.read().strip()
            if "," in line:
                gdb_status, gdb_detail = line.split(",", 1)
            else:
                gdb_status, gdb_detail = line, ""
    except subprocess.TimeoutExpired:
        pass  # gdb_status stays GDB_TIMEOUT

    # Now find out what happened to QEMU itself.
    outcome = None
    exit_code = None
    try:
        exit_code = qemu_proc.wait(timeout=QEMU_EXIT_TIMEOUT_SEC)
        if exit_code == 0:
            outcome = "PASS"
        elif exit_code == 1:
            outcome = "FAIL_SDC"
        elif exit_code < 0:
            outcome = "CRASH"  # killed by a signal
        else:
            outcome = f"OTHER_EXIT_{exit_code}"
    except subprocess.TimeoutExpired:
        outcome = "HANG"
        qemu_proc.kill()
        try:
            qemu_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    return {
        "trial_id": trial_id,
        "skip_instructions": skip,
        "fault_target": target,
        "fault_register": reg,
        "fault_address": addr,
        "fault_bit": bit,
        "gdb_status": gdb_status,
        "gdb_detail": gdb_detail,
        "qemu_exit_code": exit_code,
        "outcome": outcome,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--output", default=os.path.join(HERE, "results.csv"))
    ap.add_argument("--max-instr", type=int, default=DEFAULT_MAX_INSTR)
    ap.add_argument("--gdb-port", type=int, default=12345)
    args = ap.parse_args()

    if not os.path.exists(FIRMWARE_ELF):
        print(f"ERROR: {FIRMWARE_ELF} not found -- run `make` in firmware/ first.",
              file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)

    fieldnames = [
        "trial_id", "skip_instructions", "fault_target", "fault_register",
        "fault_address", "fault_bit", "gdb_status", "gdb_detail",
        "qemu_exit_code", "outcome",
    ]

    write_header = not os.path.exists(args.output)
    with open(args.output, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        outcome_counts = {}
        t0 = time.time()
        for i in range(args.trials):
            row = run_one_trial(i, rng, args.max_instr, args.gdb_port)
            writer.writerow(row)
            f.flush()
            outcome_counts[row["outcome"]] = outcome_counts.get(row["outcome"], 0) + 1

            elapsed = time.time() - t0
            print(
                f"\r[{i+1}/{args.trials}] {row['outcome']:<14s} "
                f"skip={row['skip_instructions']:<5d} "
                f"target={row['fault_target']:<8s} "
                f"({elapsed:.0f}s elapsed)  "
                f"tally={outcome_counts}",
                end="", flush=True,
            )
        print()

    print(f"\nDone. Wrote {args.trials} trials to {args.output}")
    print("Outcome tally:", outcome_counts)


if __name__ == "__main__":
    main()
