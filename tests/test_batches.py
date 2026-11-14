"""Checks for make_batches.drop_reason (the clear-mismatch filter) and its config. Run: python tests/test_batches.py

The drop_reason cases set the red-flag list explicitly, and the config cases point make_batches at a temporary folder,
so the result does not depend on anyone's private config. (Importing make_batches still reads the real config once.)"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_jobs  # noqa: E402
import make_batches  # noqa: E402


def P(title, desc):
    return {"title": title, "description": desc}


CITIZEN = P("Machine Learning Engineer", "You will train models. U.S. citizenship is required for this role.")
CLEARANCE = P("Machine Learning Engineer", "You will train models. An active security clearance is needed.")
NO_SPONSOR = P("Machine Learning Engineer", "You will train models. We are unable to sponsor visas for this role.")
PLAIN = P("Machine Learning Engineer", "You will train models. 1+ years of experience with PyTorch.")
SENIOR = P("Machine Learning Engineer", "You will train models. 7+ years of industry experience required.")
NON_ENG = P("Customer Success Specialist", "You will support customers.")

fail = 0


def check(name, got, want):
    global fail
    if got != want:
        print("BAD", name, "| got", repr(got), "| want", repr(want))
        fail += 1


# ---- drop_reason ----
make_batches.DROP_RED_FLAGS = []  # the public default: no red flag disqualifies a posting
check("default keeps a citizenship posting", make_batches.drop_reason(dict(CITIZEN)), None)
check("default keeps a clearance posting", make_batches.drop_reason(dict(CLEARANCE)), None)
check("default keeps a no-sponsorship posting", make_batches.drop_reason(dict(NO_SPONSOR)), None)
check("default keeps a plain posting", make_batches.drop_reason(dict(PLAIN)), None)
check("years rule", make_batches.drop_reason(dict(SENIOR)), "requires 7+ years")
check("title rule", make_batches.drop_reason(dict(NON_ENG)), "not an ML/AI engineering title")

make_batches.DROP_RED_FLAGS = ["citizenship_or_clearance"]
check("configured flag drops a citizenship posting", make_batches.drop_reason(dict(CITIZEN)),
      "requires citizenship/clearance/US-person status")
check("configured flag drops a clearance posting", make_batches.drop_reason(dict(CLEARANCE)),
      "requires citizenship/clearance/US-person status")
check("a flag that is not configured does not drop", make_batches.drop_reason(dict(NO_SPONSOR)), None)
check("configured flag leaves a plain posting alone", make_batches.drop_reason(dict(PLAIN)), None)
check("the red flag is reported before the years rule",
      make_batches.drop_reason(P("ML Engineer", "Must be a U.S. citizen. 8+ years of experience required.")),
      "requires citizenship/clearance/US-person status")

make_batches.DROP_RED_FLAGS = ["no_sponsorship_stated"]
check("another configured flag drops with its own reason", make_batches.drop_reason(dict(NO_SPONSOR)),
      "states that it does not sponsor visas")

p = dict(PLAIN)
make_batches.drop_reason(p)
check("min_years_required side effect", p.get("min_years_required"), 1)

# ---- the config behind it ----
repo = Path(__file__).resolve().parent.parent
shipped = json.loads((repo / "config.json").read_text(encoding="utf-8"))
check("config.json ships with no drop flags", shipped.get("drop_red_flags"), [])
check("every reason belongs to a known red flag", sorted(set(make_batches.RED_FLAG_REASONS) - set(fetch_jobs.RED_FLAGS)), [])
check("every red flag has a reason", sorted(set(fetch_jobs.RED_FLAGS) - set(make_batches.RED_FLAG_REASONS)), [])

check("a missing key means no flags", make_batches._drop_flags({}), [])
check("a null value means no flags", make_batches._drop_flags({"drop_red_flags": None}), [])
check("a valid list is returned as is", make_batches._drop_flags({"drop_red_flags": ["phd_required"]}), ["phd_required"])
try:
    make_batches._drop_flags({"drop_red_flags": ["citizenship"]})
    check("an unknown flag name is an error", "no error", "ValueError")
except ValueError:
    pass

real_root = make_batches.ROOT
try:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        make_batches.ROOT = t
        (t / "config.json").write_text(json.dumps({"drop_red_flags": [], "batch_size": 20}), encoding="utf-8")
        check("no private file: the public value", make_batches._config().get("drop_red_flags"), [])
        (t / "private").mkdir()
        (t / "private" / "config.local.json").write_text(json.dumps({"drop_red_flags": ["phd_required"]}), encoding="utf-8")
        check("the private file overrides the public value", make_batches._config().get("drop_red_flags"), ["phd_required"])
        check("other public keys survive the merge", make_batches._config().get("batch_size"), 20)
finally:
    make_batches.ROOT = real_root

print("failures:", fail)
sys.exit(1 if fail else 0)
