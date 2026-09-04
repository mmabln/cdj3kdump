CDJ 3000 FIRMWARE DUMP UTILITY
==============================
__Disclaimer: By opening, soldering and accessing the bootloader
console you can easily damage or brick your player. I take no responsiblity
for any action taken based on this document or by using this tool.__

This is a very basic tool and lazy vibe coded but should work.
Usage:
```console
> ./dump_image.py --help
usage: dump_image.py [-h] --port PORT [--baud BAUD] [--partition PARTITION]
                     [--file FILE] [--output OUTPUT]
                     [--load-address LOAD_ADDRESS] [--chunk-size CHUNK_SIZE]
                     [--retries RETRIES]

Dump a file from U-Boot RAM over a serial console

options:
  -h, --help            show this help message and exit
  --port PORT           Serial port, e.g. COM5 or /dev/ttyUSB0
  --baud BAUD           Serial baud rate (default: 115200)
  --partition PARTITION
                        U-Boot MMC partition, e.g. 1:1 (default: 1:1)
  --file FILE           File to load (default: /boot/Image)
  --output OUTPUT       Output file (default: Image.dump)
  --load-address LOAD_ADDRESS
                        RAM load address (default: 0x48080000)
  --chunk-size CHUNK_SIZE
                        Chunk size in bytes (default: 0x10000 = 64 KiB)
  --retries RETRIES     Retries per chunk (default: 3)
```

How to use
----------
- Solder wires to the CDJ 3K debug interface (CN9001 pads, TX/RX/GND) and connect it with a USB-to-serial converter set to 3.3V
- Open a terminal program
- Start the CDJ3k
- Interrupt the boot process by htting CTRL-C
- Close you terminal program when you want to start the dump
- Run the dump_image.py script
- Hints:
    - There are three partitions on the e(MMC):
        - 1:1 Primary boot partition
        - 1:2 Backup boot partition (apparently)
        - 1:3 Firmware update boot partition
    - The bootloader is changing the boot variables depending if you pressed the "update buttons" or not. So it knows when to boot the firmware update partition
    - You can select the partition you want to dump by using --partition option
    - If you are looking for the firmware decryption key, you want to dump the firmware update partition
 
What next?
----------
- Use binwalk to check for what you are looking for
- Look for LZ4 and GZ compressed parts in the dump
- Use the respective tool and then cpio to get the contents
- Have fun!
  
