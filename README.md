# SEU Fault-Injection Research Toolkit

A complete, working toolkit for studying how single-bit hardware faults
(the kind cosmic rays cause in real chips -- "single-event upsets," SEUs)
propagate through embedded software, and whether they get masked, cause
silent data corruption, or crash the system. Everything runs from the
command line with free, open-source tools -- no hardware required.

This is close to real published reliability-engineering methodology
(the kind used to test spacecraft and safety-critical embedded systems),
scaled down to something you can run on a laptop and turn into a research
paper.

## How it works, in one paragraph

We compile a small "workload" (a fixed-point matrix-vector multiply,
representative of an embedded control computation) into bare-metal
RISC-V firmware that checksums its own output and reports PASS/FAIL to
the outside world. We run it inside the QEMU emulator, which we control
over GDB's remote-debugging protocol: we let the CPU execute N
instructions, then we reach in and flip exactly one bit -- in a register
or in RAM -- to simulate a radiation-induced fault. We then let execution
continue and record whether the fault was masked, caused a wrong-but-
undetected result (a "silent data corruption," the outcome real fault-
tolerant systems fear most), or hung the CPU. Do this hundreds of times
with randomized fault parameters, and you get real, quantifiable
reliability data.

## Directory layout

```
seu-project/
├── firmware/           bare-metal RISC-V C program + build system
│   ├── main.c              the self-checking workload (matrix-vector + CRC32)
│   ├── start.S              startup assembly (stack/gp init, BSS clear)
│   ├── linker.ld             linker script (places code at QEMU's boot address)
│   └── Makefile
├── tools/
│   └── compute_golden.py    independent Python oracle for the expected checksum
├── injector/
│   ├── inject_one.py         GDB Python script: step, flip one bit, continue
│   └── run_campaign.py        orchestrator: runs N randomized trials, writes CSV
├── analysis/
│   └── analyze.py            statistics + charts from the campaign CSV
└── README.md              (this file)
```

## Setup

You need a RISC-V cross-compiler, QEMU with RISC-V support, and a
multi-arch GDB. On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y qemu-system-misc gcc-riscv64-linux-gnu \
                     binutils-riscv64-linux-gnu gdb-multiarch
pip install scipy matplotlib --break-system-packages
```

On Windows, the simplest path is **WSL2** (Windows Subsystem for Linux):
install WSL2 with an Ubuntu distro, then run the commands above inside it.
Everything in this project is pure command-line, so it works identically
once you're in a Linux shell -- WSL2, a Linux VM, or native Linux/macOS
all work the same way. (macOS: use Homebrew -- `brew install qemu` and
either build a riscv64-elf gcc via a tap or use the same
`riscv64-unknown-elf-gcc` toolchain from the [xPack project][xpack].)

[xpack]: https://xpack.github.io/dev-tools/riscv-none-elf-gcc/

## Quick start

```bash
# 1. Build the firmware
cd firmware
make
qemu-system-riscv64 -M virt -nographic -bios none -kernel firmware.elf -smp 1 -m 64M
echo "exit code: $?"   # should print 0 (PASS) with no other output

# 2. Run a fault-injection campaign (start small -- each trial takes ~1-2s)
cd ../injector
python3 run_campaign.py --trials 500 --output results.csv

# 3. Analyze the results
cd ../analysis
python3 analyze.py ../injector/results.csv --outdir report/
```

The campaign prints a live tally as it runs and writes one row per trial
to `results.csv`. The analysis script prints statistics to the terminal
and saves two PNG charts to `report/`.

## Understanding the outcomes

Every trial ends in one of these buckets:

- **PASS** -- the fault was masked; the final checksum still matched.
  This is the most common outcome in most real systems too.
- **FAIL_SDC** -- "silent data corruption." The program ran to completion
  and *looked* fine from the outside, but produced the wrong answer,
  caught only because our firmware double-checks itself with a checksum.
  In a real system without that check, this is the dangerous, invisible
  failure mode.
- **HANG** -- the fault sent the CPU into an infinite loop or otherwise
  unreachable state; QEMU never exited and had to be force-killed.
- **CRASH** -- QEMU itself terminated abnormally (rare; usually an
  unhandled trap at a very low level).

## Extending this for a research paper

Some directions that turn this from "a cool tool" into "a paper":

1. **Vary the workload.** Compare vulnerability rates across different
   algorithms (e.g., a sort vs. a filter vs. this matrix multiply). Do
   some computational patterns mask faults better than others?
2. **Add redundancy and measure the improvement.** Implement triple
   modular redundancy (run the computation 3x, vote on the result) or a
   simple parity/ECC scheme, then re-run the campaign and show
   quantitatively how much the FAIL_SDC rate drops.
3. **Vary the "when."** We already bucket faults into early/mid/late
   thirds of execution -- a real paper would want a proper regression or
   correlation analysis of failure rate vs. instruction index, not just
   three buckets.
4. **Compare register classes.** Do caller-saved (`t0`-`t6`) vs.
   callee-saved (`s0`-`s11`) vs. argument (`a0`-`a7`) registers show
   different vulnerability rates? Our per-register ranking output is a
   starting point.
5. **Statistical rigor.** With enough trials (a few thousand), run a
   proper chi-square or logistic regression to identify which factors
   (register class, execution phase, fault type) are statistically
   significant predictors of SDC -- `analyze.py` already computes a
   chi-square test if `scipy` is installed as a starting point.
6. **Compare to literature.** Real papers on this topic (search for
   "fault injection," "single event upset simulation," or "AVF
   architectural vulnerability factor" on Google Scholar) report FIT
   rates and AVF percentages -- see how your DIY numbers compare in
   shape, if not in absolute magnitude, to published results from actual
   spacecraft/aerospace fault-injection studies.

## Notes on reproducibility

Every trial launches a completely fresh QEMU process, so trials are
fully independent -- you can re-run the campaign with the same
`--seed` to get identical results, run different seeds to check
variance, or split trials across multiple machines/terminals and
concatenate the CSVs (each trial is one self-contained row).
