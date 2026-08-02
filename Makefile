CROSS   ?= riscv64-linux-gnu-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump

# -ffreestanding/-nostdlib: this is bare-metal, there is no OS underneath.
# -march/-mabi: match QEMU's default "virt" machine (64-bit RISC-V, general
#   extensions, double-float ABI). -mcmodel=medany is required for code
#   linked above address 0 on RV64.
CFLAGS  := -march=rv64gc -mabi=lp64d -mcmodel=medany \
           -ffreestanding -fno-pic -O1 -g -Wall -Wextra
LDFLAGS := -T linker.ld -nostdlib -static

all: firmware.elf firmware.dis

firmware.elf: start.o main.o linker.ld
	$(CC) $(CFLAGS) $(LDFLAGS) start.o main.o -o $@

start.o: start.S
	$(CC) $(CFLAGS) -c $< -o $@

main.o: main.c
	$(CC) $(CFLAGS) -c $< -o $@

# Human-readable disassembly, useful for picking fault-injection targets
# (e.g. "flip a bit while the CPU is inside the inner multiply loop").
firmware.dis: firmware.elf
	$(OBJDUMP) -d firmware.elf > $@

clean:
	rm -f *.o firmware.elf firmware.dis

.PHONY: all clean
