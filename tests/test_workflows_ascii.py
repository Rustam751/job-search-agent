"""The Workflow approval dialog refuses scripts whose bytes decode to control characters (an em dash did). Keep every
workflow script plain ASCII: write '\u2019' style escapes in JS strings instead. Run: python tests/test_workflows_ascii.py"""
import sys
from pathlib import Path

bad = []
for p in sorted((Path(__file__).resolve().parent.parent / ".claude" / "workflows").glob("*.js")):
    text = p.read_bytes().decode("utf-8")
    for i, c in enumerate(text):
        if ord(c) > 126 or (ord(c) < 32 and c != "\n"):
            bad.append(f"{p.name}: {c!r} at char {i} (line {text.count(chr(10), 0, i) + 1})")
print("\n".join(bad[:20]) or "all workflow scripts are ASCII")
print("failures:", len(bad))
sys.exit(1 if bad else 0)
