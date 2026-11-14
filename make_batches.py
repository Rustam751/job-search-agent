"""Merge posting files, drop the clear mismatches, trim each description, and write scoring batches.

Usage: python make_batches.py out_dir postings1.json [...] [--size 20] [--seen seen_urls.json] [--since YYYY-MM-DD]
Writes out_dir/batch_NN.md (what the scoring agents read), out_dir/index.json (full records), out_dir/dropped.json.
"""
import json
import re
import sys
import textwrap
from pathlib import Path

import fetch_jobs

ROOT = Path(__file__).resolve().parent


def _config():
    """config.json with the private overrides on top (the same merge daily.py does)."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    local = ROOT / "private" / "config.local.json"
    if local.exists():
        cfg.update(json.loads(local.read_text(encoding="utf-8")))
    return cfg


def _drop_flags(cfg):
    """The configured "drop_red_flags" list. An unknown name is an error, never a silent no-op."""
    flags = list(cfg.get("drop_red_flags") or [])
    unknown = [k for k in flags if k not in fetch_jobs.RED_FLAGS]
    if unknown:
        raise ValueError(f"drop_red_flags has unknown entries {unknown}; known: {sorted(fetch_jobs.RED_FLAGS)}")
    return flags


# Which fetch_jobs.RED_FLAGS disqualify a posting outright. None by default ("drop_red_flags": [] in config.json);
# list the ones that apply to you in private/config.local.json.
DROP_RED_FLAGS = _drop_flags(_config())
RED_FLAG_REASONS = {
    "citizenship_or_clearance": "requires citizenship/clearance/US-person status",
    "no_sponsorship_stated": "states that it does not sponsor visas",
    "phd_required": "requires a PhD",
}

NON_ENG_TITLE = re.compile(
    r"specialist|customer|mechanical engineer|electrical engineer|kernel|hardware|firmware|analog|asic|"
    r"\bfpga\b|verification engineer|recruit|coordinator|writer|editor|content|community|"
    r"\bassociate\b(?!.*engineer)|payroll|accountant|people partner|\bhr\b|facilities|security officer",
    re.I,
)
YEARS_REQ = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?(?:or more\s+)?years?\s+(?:of\s+)?"
    r"(?:(?:professional|industry|relevant|hands-on|work|full-time|software|engineering|ml|machine learning|"
    r"production|post-graduate|industrial|applied)\s+)*(?:experience|exp\b)",
    re.I,
)
KEEP_PARA = re.compile(
    r"requirement|qualificat|you have|you'll|you will|your experience|experience (with|in)|years|degree|"
    r"bachelor|master|ph\.?d|proficien|familiar|bonus|nice to have|plus if|responsibilit|what you.ll do|"
    r"about the role|skills|stack|we're looking|we are looking|ideal candidate|pay range|salary range|base pay",
    re.I,
)
DROP_PARA = re.compile(
    r"equal opportunity|equal employment|without regard to|reasonable accommodation|e-verify|"
    r"401\(k\)|dental|vision insurance|parental leave|commuter|wellness stipend|unlimited pto|"
    r"we celebrate diversity|background check will|privacy notice|applicant privacy",
    re.I,
)


# A years figure is not a hard requirement when the same sentence offers a degree instead, or it sits under a
# "nice to have" heading ("a plus, but not required: Master's ... and/or 5+ years").
YEARS_ALTERNATIVE = re.compile(r"(or|and/or)[^.\n]{0,60}(master|m\.s\.|advanced degree|graduate degree|ph\.?d)|"
                               r"(master|m\.s\.|advanced degree|graduate degree|ph\.?d)[^.\n]{0,60}(or|and/or)", re.I)
NICE_TO_HAVE = re.compile(r"not required|nice to have|a plus|bonus|preferred", re.I)


def min_years_required(desc):
    found = []
    for m in YEARS_REQ.finditer(desc):
        if int(m.group(1)) > 15:
            continue
        start = max(desc.rfind(".", 0, m.start()), desc.rfind("\n", 0, m.start())) + 1
        end = min(x for x in (desc.find(".", m.end()), desc.find("\n", m.end()), len(desc)) if x >= 0)
        sentence = desc[start:end]
        heading = desc[max(0, start - 300):start]
        if YEARS_ALTERNATIVE.search(sentence) or NICE_TO_HAVE.search(sentence) or NICE_TO_HAVE.search(heading.split("\n")[-1] if "\n" in heading else heading):
            continue
        found.append(int(m.group(1)))
    return min(found) if found else None


def trim(desc, cap=2200):
    paras = [p.strip() for p in desc.split("\n") if p.strip()]
    head = [p for p in paras[:4] if not DROP_PARA.search(p)]
    body = [p for p in paras[4:] if KEEP_PARA.search(p) and not DROP_PARA.search(p)]
    out, used = [], 0
    for p in head + body:
        if used + len(p) > cap:
            out.append(p[: max(0, cap - used)])
            break
        out.append(p)
        used += len(p)
    return "\n".join(out)


def drop_reason(p):
    """Why a posting is a clear mismatch, or None. Sets p["min_years_required"] as a side effect."""
    yrs = min_years_required(p["description"])
    p["min_years_required"] = yrs
    # recomputed from the text, not the red_flags stored at fetch time (older fetches used a looser pattern)
    for flag in DROP_RED_FLAGS:
        if fetch_jobs.RED_FLAGS[flag].search(p.get("description", "")):
            return RED_FLAG_REASONS.get(flag, f"red flag: {flag}")
    if NON_ENG_TITLE.search(p["title"]) and not fetch_jobs.ML_TITLE.search(p["title"]):
        return "not an ML/AI engineering title"
    if yrs is not None and yrs >= 5:
        return f"requires {yrs}+ years"
    return None


def write_batches(keep, out, size=20):
    """Write batch_NN.md files (what the scoring agents read). Returns the batch paths."""
    paths = []
    for start in range(0, len(keep), size):
        lines = []
        for i, p in enumerate(keep[start:start + size]):
            lines += [f"### POSTING {i + 1}", f"company: {p['company']} (sector: {p['category']})",
                      f"title: {p['title']}", f"location: {p['location']}  [{p['where']}]",
                      f"url: {p['url']}", f"posted: {p['posted']}",
                      f"compensation: {p.get('compensation') or 'not listed'}",
                      f"min_years_required (regex guess): {p['min_years_required']}", "description (trimmed):"]
            for para in p["description_trimmed"].split("\n"):
                lines += textwrap.wrap(para, 160) or [""]
            lines.append("")
        path = out / f"batch_{start // size:02d}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


def main():
    argv = sys.argv[1:]
    size = 20
    if "--size" in argv:
        size = int(argv[argv.index("--size") + 1])
        del argv[argv.index("--size"): argv.index("--size") + 2]
    seen = set()
    if "--seen" in argv:
        i = argv.index("--seen")
        seen = set(json.load(open(argv[i + 1], encoding="utf-8")))
        del argv[i: i + 2]
    since = ""
    if "--since" in argv:
        i = argv.index("--since")
        since = argv[i + 1]
        del argv[i: i + 2]
    out, srcs = Path(argv[0]), argv[1:]
    out.mkdir(parents=True, exist_ok=True)

    postings, urls = [], set()
    for s in srcs:
        for p in json.load(open(s, encoding="utf-8")):
            if p["url"] in urls or p["url"] in seen:
                continue
            if since and p["posted"] < since:
                continue
            urls.add(p["url"])
            postings.append(p)

    keep, dropped = [], []
    for p in postings:
        reason = drop_reason(p)
        if reason:
            dropped.append({"company": p["company"], "title": p["title"], "url": p["url"], "reason": reason})
        else:
            p["description_trimmed"] = trim(p["description"])
            keep.append(p)

    json.dump(keep, open(out / "index.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(dropped, open(out / "dropped.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    paths = write_batches(keep, out, size)
    sizes = [len(p.read_text(encoding="utf-8")) for p in paths]
    print(json.dumps({"kept": len(keep), "batches": len(sizes), "max_batch_bytes": max(sizes) if sizes else 0,
                      "dropped": len(dropped)}))


if __name__ == "__main__":
    main()
