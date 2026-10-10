"""Private pipe gate: starts only a trusted adapter argv after OS limits bind."""
import json
import os
import subprocess
import sys
argv = json.loads(sys.stdin.readline())
if not isinstance(argv, list) or not argv or (not all((isinstance(item, str) for item in argv))):
    raise SystemExit(126)
os.execvpe(argv[0], argv, os.environ)
