"""Compare the two saved JSON responses. Usage: python3 compare_reads.py RUN_DIR"""

import json, sys
from pathlib import Path
p = Path(sys.argv[1])
assert json.loads((p/"first-read.json").read_text()) == json.loads((p/"second-read.json").read_text())
print("Both reads returned the same post; verify the cache paths in the traces below")
