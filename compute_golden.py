#!/usr/bin/env python3
"""
compute_golden.py

Computes the golden (known-correct) CRC32 checksum of the matrix-vector
workload in firmware/main.c. Run this any time you change the A matrix or
x vector in main.c, and paste the printed EXPECTED_CRC value back into the
C source.

This script deliberately re-implements the matrix-vector multiply in plain
Python (not by calling the firmware) so it acts as an independent reference
oracle for the "correct" answer -- which is the whole point of a
self-checking fault-injection target.
"""
import struct
import zlib

A = [
    [3, -1,  2,  0,  1,  4, -2,  1],
    [0,  2, -1,  3,  1,  0,  1, -3],
    [1,  1,  1,  1,  1,  1,  1,  1],
    [-2,  3,  0,  1, -1,  2,  0,  2],
    [4,  0, -1,  2,  1, -2,  3,  1],
    [1, -2,  2,  0,  3,  1, -1,  0],
    [0,  1,  1, -1,  2,  0,  2, -1],
    [2,  0,  3,  1, -1,  1,  0,  2],
]
x = [5, -3, 2, 7, 0, -1, 4, 6]


def matvec(A, x):
    y = []
    for row in A:
        acc = 0
        for a, xv in zip(row, x):
            acc += a * xv
        y.append(acc)
    return y


def main():
    y = matvec(A, x)
    # pack as 8 little-endian 32-bit signed ints, same memory layout as
    # the `int y[N]` array in main.c on a little-endian RISC-V target
    buf = struct.pack('<8i', *y)
    crc = zlib.crc32(buf) & 0xFFFFFFFF
    print(f"y (golden output vector) = {y}")
    print(f"EXPECTED_CRC = 0x{crc:08X}u")


if __name__ == "__main__":
    main()
