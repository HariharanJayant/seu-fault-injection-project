/*
 * main.c -- bare-metal RISC-V "workload" for fault-injection experiments.
 *
 * The workload is a small fixed-point matrix-vector multiply, representative
 * of a simple embedded control computation (e.g. a sensor-fusion or filter
 * update). After computing the result, it runs a CRC32 checksum over the
 * output and compares it against a known-good value computed offline.
 *
 * If the checksum matches  -> PASS  (correct result, fault masked/no fault)
 * If the checksum mismatches -> FAIL (a fault corrupted the computation --
 *                                       this is a "Silent Data Corruption",
 *                                       the outcome fault-tolerant systems
 *                                       fear most)
 *
 * Result is reported to the outside world (the QEMU host process) via the
 * SiFive "test" finisher device memory-mapped at 0x100000, which is present
 * on QEMU's "virt" RISC-V machine. Writing certain magic values there causes
 * QEMU itself to exit with a matching process exit code, which our Python
 * fault-injection harness reads directly -- no UART parsing required.
 */

#define TEST_DEVICE_ADDR 0x100000
#define FINISHER_FAIL   0x3333
#define FINISHER_PASS   0x5555

static volatile unsigned int * const test_device = (unsigned int *)TEST_DEVICE_ADDR;

static void sim_exit(int pass, int code) {
    unsigned int value;
    if (pass) {
        value = FINISHER_PASS;
    } else {
        /* upper 16 bits carry an exit code, lower 16 bits are the FAIL magic */
        value = ((unsigned int)code << 16) | FINISHER_FAIL;
    }
    *test_device = value;
    for (;;) { /* should not return; spin just in case */ }
}

/* ---- Fixed 8x8 matrix and 8-vector "sensor input" ---- */
#define N 8

static const int A[N][N] = {
    { 3, -1,  2,  0,  1,  4, -2,  1},
    { 0,  2, -1,  3,  1,  0,  1, -3},
    { 1,  1,  1,  1,  1,  1,  1,  1},
    {-2,  3,  0,  1, -1,  2,  0,  2},
    { 4,  0, -1,  2,  1, -2,  3,  1},
    { 1, -2,  2,  0,  3,  1, -1,  0},
    { 0,  1,  1, -1,  2,  0,  2, -1},
    { 2,  0,  3,  1, -1,  1,  0,  2},
};

static const int x[N] = {5, -3, 2, 7, 0, -1, 4, 6};

/* CRC32 (poly 0xEDB88320), implemented without any library dependency */
static unsigned int crc32_bytes(const unsigned char *data, unsigned int len) {
    unsigned int crc = 0xFFFFFFFFu;
    for (unsigned int i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            unsigned int mask = -(crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return ~crc;
}

/*
 * EXPECTED_CRC is the golden checksum of the correct output vector `y`,
 * computed offline (see tools/compute_golden.py) for this exact A and x.
 * If you change A or x above, regenerate this constant.
 */
#define EXPECTED_CRC 0xEFE4A546u

int main(void) {
    int y[N];

    /* the actual "workload": matrix-vector multiply */
    for (int i = 0; i < N; i++) {
        int acc = 0;
        for (int j = 0; j < N; j++) {
            acc += A[i][j] * x[j];
        }
        y[i] = acc;
    }

    unsigned int crc = crc32_bytes((const unsigned char *)y, sizeof(y));

    if (crc == EXPECTED_CRC) {
        sim_exit(1, 0);   /* PASS */
    } else {
        sim_exit(0, 1);   /* FAIL: silent data corruption detected */
    }

    return 0;
}
