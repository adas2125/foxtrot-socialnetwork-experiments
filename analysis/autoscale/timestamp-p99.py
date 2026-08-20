#!/usr/bin/env python3

import os
import signal
import sys
import time

source_path = sys.argv[1]
output_path = sys.argv[2]
running = True

def stop_handler(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)

while running and not os.path.exists(source_path):
    time.sleep(0.02)

if not running:
    sys.exit(0)

with open(source_path, "r") as source, \
     open(output_path, "w") as output:

    output.write("epoch_ns\tp99_us\n")
    output.flush()

    while running:
        line = source.readline()

        if line:
            value = line.strip()

            if value:
                output.write(f"{time.time_ns()}\t{value}\n")
                output.flush()
        else:
            time.sleep(0.02)
