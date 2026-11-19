#!/usr/bin/env python3
"""Debug script to check dynamic symbol injection in generated schematic."""

import re

with open("/home/boltzmann6/arduino_nano.kicad_sch", "r") as f:
    content = f.read()

# Check what's in lib_symbols now
lib_start = content.find("(lib_symbols")
if lib_start >= 0:
    depth = 0
    lib_end = lib_start
    for i in range(lib_start, len(content)):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
            if depth == 0:
                lib_end = i
                break
    lib_section = content[lib_start : lib_end + 1]

    # Find all top-level symbol names
    symbols = re.findall(r'\(symbol\s+"([^"]+)"', lib_section)
    top_level = [s for s in symbols if ":" in s]
    print(f"Top-level symbols in lib_symbols ({len(top_level)}):")
    for s in top_level:
        print(f"  {s}")

# Check if ATmega328P-A has pin definitions in the file
atmega_start = content.find('"MCU_Microchip_ATmega:ATmega328P-A"')
if atmega_start >= 0:
    open_paren = content.rfind("(symbol", 0, atmega_start)
    depth = 0
    j = open_paren
    while j < len(content):
        if content[j] == "(":
            depth += 1
        elif content[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    block = content[open_paren : j + 1]

    # Check for extends
    extends = re.findall(r'\(extends\s+"([^"]+)"', block)
    if extends:
        print(f"  ATmega328P-A has extends: {extends}")
    else:
        print("  ATmega328P-A has NO extends (fully inlined)")

    # Check for pin definitions
    pins_in_block = re.findall(r'\(number\s+"([^"]+)"', block)
    print(f"  Pin numbers found in block: {len(pins_in_block)}")

    # Check for sub-symbols
    subsymbols = re.findall(r'\(symbol\s+"(ATmega[^"]+)"', block)
    print(f"  Sub-symbols: {subsymbols}")

    # Check size of block
    print(f"  Block size: {len(block)} chars")
else:
    print("ATmega328P-A NOT found in lib_symbols!")

# Check CH340G
ch340_start = content.find('"Interface_USB:CH340G"')
if ch340_start >= 0:
    print("\n  CH340G found in lib_symbols")
    open_paren = content.rfind("(symbol", 0, ch340_start)
    depth = 0
    j = open_paren
    while j < len(content):
        if content[j] == "(":
            depth += 1
        elif content[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    block = content[open_paren : j + 1]
    extends = re.findall(r'\(extends\s+"([^"]+)"', block)
    if extends:
        print(f"  CH340G has extends: {extends}")
    pins_in_block = re.findall(r'\(number\s+"([^"]+)"', block)
    print(f"  Pin numbers found: {len(pins_in_block)}")
else:
    print("\n  CH340G NOT found in lib_symbols!")

# Check USB_B_Micro
usb_start = content.find('"Connector:USB_B_Micro"')
if usb_start >= 0:
    print("\n  USB_B_Micro found in lib_symbols")
else:
    print("\n  USB_B_Micro NOT found in lib_symbols!")

# Check Conn_01x15
conn_start = content.find('"Connector_Generic:Conn_01x15"')
if conn_start >= 0:
    print("  Conn_01x15 found in lib_symbols")
else:
    print("  Conn_01x15 NOT found in lib_symbols!")

# Check instances
print("\nComponent instances in schematic:")
for m in re.finditer(r'\(lib_id\s+"([^"]+)"\)', content):
    print(f"  {m.group(1)}")
