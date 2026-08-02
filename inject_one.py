"""
inject_one.py -- runs INSIDE gdb's Python interpreter (via `gdb -x`).

Performs exactly one fault-injection trial against a QEMU RISC-V target that
is already running and waiting for a debugger (QEMU was started with
`-s -S`, i.e. GDB stub on :1234, halted at the reset vector).

What one trial does:
  1. Attach to QEMU over the GDB remote protocol.
  2. Single-step the target for `SKIP_INSTRUCTIONS` instructions. This is
     how we control *when* (i.e. at what point in the workload) the fault
     lands -- early, middle, or late in the matrix-multiply computation.
  3. Flip exactly one bit, either in a general-purpose register or in a
     word of RAM, depending on FAULT_TARGET / FAULT_ADDR / FAULT_BIT.
     This simulates a single-event upset (SEU): the kind of bit-flip a
     cosmic ray or radiation particle can induce in a real chip.
  4. Let the program run to completion (or timeout).
  5. Read QEMU's process exit code to classify the trial's outcome.

All trial parameters come in as environment variables (set by the Python
orchestrator, run_campaign.py) since gdb's `-x script.py` doesn't accept
custom command-line arguments cleanly. This keeps this script simple and
keeps all the experiment-design logic (random sampling, campaign looping,
CSV writing) in ordinary Python outside of gdb.
"""

import gdb
import os
import random
import sys

GDB_PORT = os.environ["INJECT_GDB_PORT"]
SKIP_INSTRUCTIONS = int(os.environ["INJECT_SKIP_INSTRUCTIONS"])
FAULT_TARGET = os.environ["INJECT_FAULT_TARGET"]      # "register" or "memory"
FAULT_REG = os.environ.get("INJECT_FAULT_REG", "")    # e.g. "a5"
FAULT_ADDR = os.environ.get("INJECT_FAULT_ADDR", "")  # e.g. "0x80004000" (hex str)
FAULT_BIT = int(os.environ["INJECT_FAULT_BIT"])       # 0-31
RESULT_FILE = os.environ["INJECT_RESULT_FILE"]


def write_result(status, detail=""):
    """Write the trial outcome to a small file the orchestrator reads back.
    (We can't just return a Python value -- gdb -x runs as a subprocess.)"""
    with open(RESULT_FILE, "w") as f:
        f.write(f"{status},{detail}\n")


def main():
    gdb.execute(f"target remote :{GDB_PORT}", to_string=True)

    # Step past the QEMU reset ROM + our startup code, into the workload,
    # to the point where the fault should land.
    try:
        gdb.execute(f"stepi {SKIP_INSTRUCTIONS}", to_string=True)
    except gdb.error as e:
        # The program may exit/trap before reaching the requested step
        # count (e.g. an earlier injected fault already crashed it in a
        # prior malformed run) -- treat as a setup failure, not a result.
        write_result("SETUP_ERROR", str(e))
        return

    # --- Inject exactly one bit-flip fault ---
    if FAULT_TARGET == "register":
        try:
            before = int(gdb.parse_and_eval(f"${FAULT_REG}"))
        except gdb.error as e:
            write_result("SETUP_ERROR", f"bad register {FAULT_REG}: {e}")
            return
        after = before ^ (1 << FAULT_BIT)
        gdb.execute(f"set ${FAULT_REG} = {after}", to_string=True)
        detail = f"reg={FAULT_REG} bit={FAULT_BIT} before=0x{before & 0xffffffff:08x} after=0x{after & 0xffffffff:08x}"

    elif FAULT_TARGET == "memory":
        addr = int(FAULT_ADDR, 16)
        try:
            before = int(gdb.parse_and_eval(f"*(unsigned int *){addr}"))
        except gdb.error as e:
            write_result("SETUP_ERROR", f"bad address {FAULT_ADDR}: {e}")
            return
        after = before ^ (1 << FAULT_BIT)
        gdb.execute(f"set *(unsigned int *){addr} = {after}", to_string=True)
        detail = f"addr={FAULT_ADDR} bit={FAULT_BIT} before=0x{before & 0xffffffff:08x} after=0x{after & 0xffffffff:08x}"

    else:
        write_result("SETUP_ERROR", f"unknown fault target {FAULT_TARGET}")
        return

    # --- Let it run. The firmware itself decides PASS/FAIL and exits QEMU
    # via the sifive_test finisher device; we don't have to guess. ---
    try:
        gdb.execute("continue", to_string=True)
    except gdb.error:
        # "continue" raises once the inferior exits -- this is the normal,
        # expected path, not an error.
        pass

    # If we get here without a timeout (enforced by the orchestrator's
    # subprocess timeout, not by gdb itself), the process has exited.
    # We don't know the exit code from inside gdb reliably across all
    # versions, so the orchestrator reads QEMU's actual process exit code
    # from the shell -- this script just confirms injection happened.
    write_result("INJECTED_AND_RAN", detail)


main()
try:
    gdb.execute("quit", to_string=True)
except Exception:
    pass
