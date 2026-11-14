"""Deterministic no-fabrication check: a tailored résumé may only use facts present in the master résumé.

Usage: python resume_guard.py master.json tailored.json   (exit code 1 on violations)

Checks:
  1. Contact block is identical to the master's.
  2. Every bullet cites source_ids that exist in the master.
  3. Every standalone number in a bullet appears in the text of the bullets it cites.
  4. Every number in the summary appears somewhere in the master.
  5. Every listed skill exists in the master skills (case-insensitive).
  6. Every entry's dates string exists in the master.
It cannot judge meaning (e.g. a target stated as achieved) — that is the LLM reviewer's job.
"""
import json
import re
import sys

# A standalone number, including one with thousands separators or a magnitude suffix ('1,200', '10x', '$5M', '2k').
NUM = re.compile(r"(?<![A-Za-z0-9.,])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?=[kKmMbBxX]?(?![A-Za-z0-9]))")


def numbers(text):
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in NUM.findall(text.replace("**", ""))}


def master_index(master):
    bullets, dates, blob = {}, set(), json.dumps(master, ensure_ascii=False)
    for group in ("experience", "projects"):
        for item in master[group]:
            dates.add(item["dates"])
            for b in item["bullets"]:
                bullets[b["id"]] = b["text"]
    for e in master["education"]:
        dates.add(e["dates"])
    skills = {s.lower() for items in master["skills"].values() for s in items}
    return bullets, dates, skills, blob


def check(master, tailored):
    bullets, dates, skills, blob = master_index(master)
    problems = []
    for k, v in tailored.get("contact", {}).items():
        if master["contact"].get(k) != v:
            problems.append(f"contact field {k!r} differs from master")
    extra = numbers(tailored.get("summary", "")) - numbers(blob)
    if extra:
        problems.append(f"summary uses numbers not in master: {sorted(extra)}")
    for group in tailored.get("skills", []):
        for s in group["items"]:
            if s.lower() not in skills:
                problems.append(f"skill not in master: {s!r}")
    for sec in tailored.get("sections", []):
        for e in sec["entries"]:
            for key in ("heading_right", "subheading_right"):
                val = e.get(key, "")
                if val and re.search(r"\d{4}", val) and val not in dates:
                    problems.append(f"{e['heading']}: dates {val!r} not in master")
            for b in e.get("bullets", []):
                ids = b.get("source_ids") or []
                missing = [i for i in ids if i not in bullets]
                if not ids or missing:
                    problems.append(f"{e['heading']}: bullet without valid source_ids {missing or '[]'}: {b['text'][:70]}")
                    continue
                allowed = set().union(*(numbers(bullets[i]) for i in ids))
                extra = numbers(b["text"]) - allowed
                if extra:
                    problems.append(f"{e['heading']}: numbers {sorted(extra)} not in cited sources {ids}: {b['text'][:70]}")
    return problems


if __name__ == "__main__":
    m = json.load(open(sys.argv[1], encoding="utf-8"))
    t = json.load(open(sys.argv[2], encoding="utf-8"))
    issues = check(m, t)
    print("\n".join(issues) if issues else "OK: no unsupported facts found")
    sys.exit(1 if issues else 0)
