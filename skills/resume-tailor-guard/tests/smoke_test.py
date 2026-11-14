"""Smoke test: run the scripts the way an agent does, on the bundled example.

The guard must accept the example and must reject seeded fabrications: an invented number, an invented skill,
a changed date, and a magnitude glued onto a true number.
Run from anywhere:  python tests/smoke_test.py   (exit code 1 on any failure)
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS, EXAMPLES = ROOT / "scripts", ROOT / "examples"
MASTER = EXAMPLES / "master_resume.json"
failures = 0


def run(script, *args):
    p = subprocess.run([sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout + p.stderr


def ok(what, passed, detail=""):
    global failures
    failures += not passed
    print(("PASS  " if passed else "FAIL  ") + what + ("" if passed else f"\n      {detail.strip()}"))


def seeded(tmp, name, change):
    """A copy of the example with one fabrication seeded into its résumé."""
    app = copy.deepcopy(EXAMPLE)
    change(app["resume"])
    path = Path(tmp) / f"{name}.json"
    path.write_text(json.dumps(app, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def invented_number(resume):   # the master says 14 DAGs
    bullet = resume["sections"][0]["entries"][0]["bullets"][0]
    assert "**14 Apache Airflow DAGs**" in bullet["text"]
    bullet["text"] = bullet["text"].replace("**14 Apache Airflow DAGs**", "**40 Apache Airflow DAGs**")


def invented_skill(resume):    # the posting lists Kubernetes as nice to have; the master does not have it
    resume["skills"][3]["items"].append("Kubernetes")


def changed_date(resume):      # the master says Jun 2021
    entry = resume["sections"][0]["entries"][0]
    assert entry["dates"] == "Jun 2021 – Aug 2024"
    entry["dates"] = "Jun 2019 – Aug 2024"


EXAMPLE = json.loads((EXAMPLES / "tailored.json").read_text(encoding="utf-8"))

code, out = run("resume_guard.py", MASTER)
ok("the example master is valid and confirmed", code == 0 and "confirmed by the candidate" in out, out)

code, out = run("resume_guard.py", MASTER, EXAMPLES / "tailored.json")
ok("the guard accepts the example", code == 0 and out.startswith("OK:"), out)

code, out = run("check_shape.py", EXAMPLES / "tailored.json", "--final")
ok("the shape check accepts the example as final", code == 0 and out.startswith("OK:"), out)

code, out = run("style_check.py", EXAMPLES / "tailored.json")
ok("the optional style check accepts the example", code == 0, out)

with tempfile.TemporaryDirectory() as tmp:
    cases = [("an invented number", invented_number, "numbers ['40'] are not in the cited master bullets ['ql1']"),
             ("an invented skill", invented_skill, "skill not in the master skills: 'Kubernetes'"),
             ("a changed date", changed_date, "Quillmere Logistics: dates 'Jun 2019 – Aug 2024' differs from the "
                                              "master entry ('Jun 2021 – Aug 2024')")]
    for what, change, message in cases:
        code, out = run("resume_guard.py", MASTER, seeded(tmp, what.replace(" ", "_"), change))
        ok(f"the guard rejects {what}", code == 1 and message in out and out.count("\n- ") + out.startswith("- ") == 1,
           f"exit {code}: {out}")

    def all_three(resume):
        for _, change, _ in cases:
            change(resume)

    def glued_magnitude(resume):  # 14 is true, 14-thousand is not
        bullet = resume["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] = bullet["text"].replace("**14 Apache Airflow DAGs**", "**14-thousand Apache Airflow DAGs**")

    code, out = run("resume_guard.py", MASTER, seeded(tmp, "glued_magnitude", glued_magnitude))
    ok("the guard rejects a magnitude glued onto a true number",
       code == 1 and "numbers ['14-thousand'] are not in the cited master bullets ['ql1']" in out, f"exit {code}: {out}")

    out_dir = Path(tmp) / "md"
    code, out = run("resume_guard.py", MASTER, seeded(tmp, "all_three", all_three), "--markdown", out_dir)
    ok("the guard reports all three at once and writes no Markdown",
       code == 1 and all(message in out for _, _, message in cases) and "3 problem(s)" in out and not out_dir.exists(),
       f"exit {code}: {out}")

    code, out = run("resume_guard.py", MASTER, EXAMPLES / "tailored.json", "--markdown", out_dir)
    same = code == 0 and all((out_dir / n).read_text(encoding="utf-8") == (EXAMPLES / "output" / n).read_text(encoding="utf-8")
                             for n in ("resume.md", "cover_letter.md"))
    ok("the Markdown written for the example matches examples/output", same, out)

print(f"\nfailures: {failures}")
sys.exit(1 if failures else 0)
