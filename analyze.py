#!/usr/bin/env python3
"""
analyze.py -- statistical analysis of a fault-injection campaign's results.

Reads the CSV produced by injector/run_campaign.py and produces:
  1. Overall outcome distribution (PASS / FAIL_SDC / HANG / CRASH rates),
     the key "vulnerability" numbers for a fault-injection study.
  2. A breakdown by fault target (register vs. memory).
  3. A breakdown by *when* in the program the fault landed (early / mid /
     late thirds of execution) -- do faults early in the matrix multiply
     propagate differently than faults near the CRC check itself?
  4. A per-register vulnerability ranking (which registers, when
     corrupted, are most likely to cause a wrong-but-undetected-by-you
     result if you had no checksum -- i.e. which are architecturally most
     "vulnerable").
  5. A simple chi-square-style comparison between register-target and
     memory-target failure rates, printed with the raw contingency table
     so the reader can judge significance themselves (a full chi-square
     test is included if scipy is available; otherwise the contingency
     table alone is shown).
  6. Saves a bar chart (outcome rate by fault target) and a chart of
     failure rate by execution-phase to PNG files.

Usage:
    python3 analyze.py results.csv --outdir report/
"""

import argparse
import csv
import os
from collections import Counter, defaultdict


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def phase_of(skip, max_instr):
    third = max_instr / 3
    if skip < third:
        return "early"
    elif skip < 2 * third:
        return "mid"
    else:
        return "late"


def pct(n, total):
    return 100.0 * n / total if total else 0.0


def print_table(title, counter, total):
    print(f"\n{title}")
    print("-" * len(title))
    for key, n in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"  {key:<20s} {n:>6d}  ({pct(n, total):5.1f}%)")
    print(f"  {'TOTAL':<20s} {total:>6d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--max-instr", type=int, default=2800,
                     help="must match --max-instr used in run_campaign.py")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    rows = load_rows(args.csv_path)
    total = len(rows)
    if total == 0:
        print("No rows found in", args.csv_path)
        return

    # --- 1. Overall outcome distribution ---
    outcome_counts = Counter(r["outcome"] for r in rows)
    print_table("Overall outcome distribution", outcome_counts, total)

    # --- 2. By fault target ---
    by_target = defaultdict(Counter)
    for r in rows:
        by_target[r["fault_target"]][r["outcome"]] += 1

    print("\nOutcome rate by fault target")
    print("-" * 40)
    targets = sorted(by_target.keys())
    all_outcomes = sorted(outcome_counts.keys())
    header = f"  {'outcome':<14s}" + "".join(f"{t:>12s}" for t in targets)
    print(header)
    for outcome in all_outcomes:
        line = f"  {outcome:<14s}"
        for t in targets:
            n = by_target[t].get(outcome, 0)
            tgt_total = sum(by_target[t].values())
            line += f"{pct(n, tgt_total):>10.1f}% "
        print(line)

    # --- 3. By execution phase ---
    by_phase = defaultdict(Counter)
    for r in rows:
        phase = phase_of(int(r["skip_instructions"]), args.max_instr)
        by_phase[phase][r["outcome"]] += 1

    print("\nOutcome rate by execution phase (early/mid/late thirds)")
    print("-" * 60)
    phases = ["early", "mid", "late"]
    header = f"  {'outcome':<14s}" + "".join(f"{p:>12s}" for p in phases)
    print(header)
    for outcome in all_outcomes:
        line = f"  {outcome:<14s}"
        for p in phases:
            n = by_phase[p].get(outcome, 0)
            phase_total = sum(by_phase[p].values())
            line += f"{pct(n, phase_total):>10.1f}% "
        print(line)

    # --- 4. Per-register vulnerability ranking ---
    reg_rows = [r for r in rows if r["fault_target"] == "register" and r["fault_register"]]
    reg_fail = Counter()
    reg_total = Counter()
    for r in reg_rows:
        reg_total[r["fault_register"]] += 1
        if r["outcome"] in ("FAIL_SDC", "HANG", "CRASH"):
            reg_fail[r["fault_register"]] += 1

    print("\nPer-register vulnerability (fraction of flips causing FAIL/HANG/CRASH)")
    print("-" * 70)
    ranking = []
    for reg, n in reg_total.items():
        rate = pct(reg_fail[reg], n)
        ranking.append((rate, reg, reg_fail[reg], n))
    ranking.sort(reverse=True)
    for rate, reg, nfail, n in ranking:
        if n >= 2:  # skip registers with too few samples to say anything
            print(f"  {reg:<6s} {rate:5.1f}%   ({nfail}/{n} trials)")

    # --- 5. register vs memory contingency table (for a chi-square test) ---
    print("\nContingency table: fault target vs. (masked vs. unmasked)")
    print("-" * 60)
    print(f"  {'':<10s} {'masked (PASS)':>16s} {'unmasked (other)':>18s}")
    contingency = {}
    for t in targets:
        masked = by_target[t].get("PASS", 0)
        unmasked = sum(by_target[t].values()) - masked
        contingency[t] = (masked, unmasked)
        print(f"  {t:<10s} {masked:>16d} {unmasked:>18d}")

    try:
        from scipy.stats import chi2_contingency
        table = [list(contingency[t]) for t in targets]
        chi2, p, dof, expected = chi2_contingency(table)
        print(f"\n  chi-square = {chi2:.3f}, p-value = {p:.4f}, dof = {dof}")
        if p < 0.05:
            print("  -> statistically significant difference between targets (p < 0.05)")
        else:
            print("  -> no statistically significant difference detected (p >= 0.05)")
    except ImportError:
        print("\n  (install scipy for an automatic chi-square significance test:")
        print("   pip install scipy --break-system-packages)")

    # --- 6. Plots ---
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Plot A: outcome rate by fault target
        fig, ax = plt.subplots(figsize=(7, 5))
        width = 0.35
        x = range(len(all_outcomes))
        for i, t in enumerate(targets):
            heights = [pct(by_target[t].get(o, 0), sum(by_target[t].values())) for o in all_outcomes]
            offset = (i - (len(targets) - 1) / 2) * width
            ax.bar([xi + offset for xi in x], heights, width, label=t)
        ax.set_xticks(list(x))
        ax.set_xticklabels(all_outcomes, rotation=20)
        ax.set_ylabel("Percent of trials")
        ax.set_title("Outcome rate by fault-injection target")
        ax.legend()
        fig.tight_layout()
        out1 = os.path.join(args.outdir, "outcome_by_target.png")
        fig.savefig(out1, dpi=150)
        print(f"\nSaved plot: {out1}")

        # Plot B: outcome rate by execution phase
        fig2, ax2 = plt.subplots(figsize=(7, 5))
        for i, o in enumerate(all_outcomes):
            heights = [pct(by_phase[p].get(o, 0), sum(by_phase[p].values())) for p in phases]
            ax2.plot(phases, heights, marker="o", label=o)
        ax2.set_ylabel("Percent of trials")
        ax2.set_title("Outcome rate by execution phase")
        ax2.legend()
        fig2.tight_layout()
        out2 = os.path.join(args.outdir, "outcome_by_phase.png")
        fig2.savefig(out2, dpi=150)
        print(f"Saved plot: {out2}")

    except ImportError:
        print("\n  (install matplotlib for automatic charts:")
        print("   pip install matplotlib --break-system-packages)")


if __name__ == "__main__":
    main()
