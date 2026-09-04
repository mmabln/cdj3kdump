#!/usr/bin/env python3

import argparse
import re
import sys
import time
import zlib
from pathlib import Path

import serial


PROMPT = b"=> "
DEFAULT_LOAD_ADDR = 0x48080000
SER_TIMEOUT = 0.2
CMD_TIMEOUT = 10

# Example:
# 48080000: 142fffff91005a4d 0000000000080000    MZ..../.........
MD_RE = re.compile(
    rb"^([0-9a-fA-F]+):\s+"
    rb"([0-9a-fA-F]{16})\s+"
    rb"([0-9a-fA-F]{16})"
)


class UBoot:
    def __init__(self, ser: serial.Serial):
        self.ser = ser


    def read_until_prompt(self) -> bytes:
        """Read until the U-Boot prompt appears."""

        data = bytearray()

        timeout_counter = 0

        while True:
            chunk = self.ser.read(4096)

            if not chunk:
                timeout_counter += SER_TIMEOUT
                if timeout_counter >= CMD_TIMEOUT:
                    raise TimeoutError(
                        "Timed out waiting for U-Boot prompt"
                    )

            data += chunk

            if data.endswith(PROMPT):
                return bytes(data)


    def sync(self):
        """
        Synchronize with an existing U-Boot prompt.
        Sends a newline and waits for the next prompt.
        """
        self.ser.reset_input_buffer()
        self.ser.write(b" \r")
        self.ser.flush()

        return self.read_until_prompt()
    

    def command(self, command: str) -> bytes:
        """
        Execute a U-Boot command and return everything preceding the prompt.
        """
        self.ser.write(command.encode("ascii") + b"\r")
        self.ser.flush()

        response = self.read_until_prompt()

        # Remove our command echo if present.
        lines = response.splitlines()

        if lines and command.encode("ascii") in lines[0]:
            lines = lines[1:]

        return b"\n".join(lines)


    def ext2load(
        self,
        partition: str,
        address: int,
        filename: str
    ) -> int:
        """
        Load a file using ext2load and return the number of bytes loaded.
        """
        cmd = f"ext2load mmc {partition} 0x{address:x} {filename}"

        print(f"Loading: {cmd}")

        response = self.command(cmd)

        text = response.decode("ascii", errors="replace")

        # Example:
        # 100542976 bytes read in 2333 ms (41.1 MiB/s)
        match = re.search(r"(\d+)\s+bytes\s+read", text)

        if not match:
            print(text, file=sys.stderr)
            raise RuntimeError("Could not determine loaded file size")

        size = int(match.group(1))

        print(
            f"Loaded {size:,} bytes "
            f"({size / 1024 / 1024:.2f} MiB)"
        )

        return size


    def crc32(self, address: int, length: int) -> int:
        """
        Execute U-Boot crc32 and return its CRC value.
        """
        cmd = f"crc32 0x{address:x} 0x{length:x}"

        response = self.command(cmd)

        text = response.decode("ascii", errors="replace")

        # Typical output:
        #
        # CRC32 for 48080000 ... 4808ffff ==> 12345678
        #
        match = re.search(r"=>\s*([0-9a-fA-F]{8})", text)

        if not match:
            print(text, file=sys.stderr)
            raise RuntimeError("Could not parse U-Boot CRC32 result")

        return int(match.group(1), 16)


    def md_q(self, address: int, length: int) -> bytes:
        """
        Dump 'length' bytes using U-Boot's md.q and reconstruct the
        original memory bytes.

        length must currently be a multiple of 8.
        """
        if length % 8 != 0:
            raise ValueError("md.q length must be a multiple of 8")

        count = length // 8

        response = self.command(f"md.q 0x{address:x} 0x{count:x}")

        result = bytearray()

        for line in response.splitlines():
            match = MD_RE.match(line)

            if not match:
                continue

            word1 = match.group(2)
            word2 = match.group(3)

            # U-Boot prints the 64-bit value numerically. Since the
            # machine is little-endian, convert each 64-bit word back
            # into its actual byte order.
            result.extend(bytes.fromhex(word1.decode())[::-1])
            result.extend(bytes.fromhex(word2.decode())[::-1])

        if len(result) != length:
            raise RuntimeError(
                f"md.q returned {len(result)} bytes, "
                f"expected {length}"
            )

        return bytes(result)


def dump_image(
    port: str,
    baudrate: int,
    partition: str,
    filename: str,
    output: Path,
    load_addr: int,
    chunk_size: int,
    retries: int,
):
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")

    # md.q operates on 64-bit objects.
    if chunk_size % 8:
        raise ValueError("chunk size must be a multiple of 8")

    print(f"Opening {port} at {baudrate} baud")

    with serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=SER_TIMEOUT,
        write_timeout=5.0,
    ) as ser:

        ub = UBoot(ser)

        print("Synchronizing with U-Boot...")
        ub.sync()

        # Load the complete image into RAM.
        image_size = ub.ext2load(
            partition,
            load_addr,
            filename,
        )

        if image_size == 0:
            raise RuntimeError("U-Boot reported zero-sized image")

        chunks = (image_size + chunk_size - 1) // chunk_size

        print(
            f"Chunk size : {chunk_size:,} bytes "
            f"({chunk_size / 1024:.1f} KiB)"
        )
        print(f"Chunks     : {chunks}")
        print(f"Output     : {output}")
        print()

        # Start from an empty file. We only append a chunk after its
        # CRC has been verified.
        with output.open("wb") as fp:

            for chunk_no in range(chunks):
                offset = chunk_no * chunk_size
                length = min(chunk_size, image_size - offset)
                address = load_addr + offset

                print(
                    f"[{chunk_no + 1:4d}/{chunks}] "
                    f"offset=0x{offset:08x} "
                    f"length={length:,}",
                    end=" ",
                    flush=True,
                )

                # md.q requires multiples of 8.
                # The actual Image currently happens to have a size
                # divisible by 8, but handle the generic case by
                # padding the RAM dump length.
                dump_length = (length + 7) & ~7

                for attempt in range(1, retries + 1):
                    try:
                        # 1. Calculate CRC on the target.
                        target_crc = ub.crc32(address, length)

                        # 2. Retrieve the same bytes through md.q.
                        data = ub.md_q(address, dump_length)

                        # Only retain bytes that belong to this chunk.
                        data = data[:length]

                        # 3. Calculate CRC locally.
                        local_crc = zlib.crc32(data) & 0xFFFFFFFF

                        # 4. Compare.
                        if local_crc != target_crc:
                            raise ValueError(
                                f"CRC mismatch "
                                f"(U-Boot={target_crc:08x}, "
                                f"PC={local_crc:08x})"
                            )

                        # 5. CRC is correct, so append it.
                        fp.write(data)
                        fp.flush()

                        print(
                            f"OK CRC={local_crc:08x}"
                            + (
                                f" retry={attempt}"
                                if attempt > 1
                                else ""
                            )
                        )

                        break

                    except Exception as exc:
                        if attempt >= retries:
                            print(
                                f"FAILED after {retries} attempts: {exc}"
                            )
                            raise

                        print(
                            f"ERROR: {exc}; "
                            f"retry {attempt + 1}/{retries}",
                            flush=True,
                        )

            # Sanity check.
            final_size = output.stat().st_size

            if final_size != image_size:
                raise RuntimeError(
                    f"Output size is {final_size:,}, "
                    f"expected {image_size:,}"
                )

    print()
    print("Dump completed successfully.")
    print(f"Size: {final_size:,} bytes")
    print(f"File: {output}")


def main():
    parser = argparse.ArgumentParser(
        description="Dump a file from U-Boot RAM over a serial console"
    )

    parser.add_argument(
        "--port",
        required=True,
        help="Serial port, e.g. COM5 or /dev/ttyUSB0",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200)",
    )

    parser.add_argument(
        "--partition",
        default="1:1",
        help="U-Boot MMC partition, e.g. 1:1 (default: 1:1)",
    )

    parser.add_argument(
        "--file",
        default="/boot/Image",
        help="File to load (default: /boot/Image)",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("Image.dump"),
        help="Output file (default: Image.dump)",
    )

    parser.add_argument(
        "--load-address",
        type=lambda x: int(x, 0),
        default=DEFAULT_LOAD_ADDR,
        help="RAM load address (default: 0x48080000)",
    )

    parser.add_argument(
        "--chunk-size",
        type=lambda x: int(x, 0),
        default=0x10000,
        help="Chunk size in bytes (default: 0x10000 = 64 KiB)",
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries per chunk (default: 3)",
    )

    args = parser.parse_args()

    dump_image(
        port=args.port,
        baudrate=args.baud,
        partition=args.partition,
        filename=args.file,
        output=args.output,
        load_addr=args.load_address,
        chunk_size=args.chunk_size,
        retries=args.retries,
    )


if __name__ == "__main__":
    main()
