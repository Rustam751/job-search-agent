#!/usr/bin/env python3
"""Optional style check for the prose in a tailored application: the résumé summary and the cover letter.

Usage: python style_check.py TAILORED.json [--company NAME]

It flags wording that makes a letter read as machine-written. These are house rules, not facts. Edit the lists
below to taste, or skip this step.

  - em dashes, double dashes, semicolons, and an en dash used as a sentence dash (an en dash in a range is fine)
  - emojis
  - a list of worn-out words and phrases ("leverage", "passionate", "showcase", "seamless", "serves as" ...)
  - the constructions "not only X but also Y", "not just X but Y", "It's not X, it's Y" and "From X to Y, ..."
  - wording that apologises for a gap ("I don't have experience with ...", "unfortunately")
  - any sentence longer than 40 words

The employer's name is exempt, because a company can be called Harness or Seamless. It is read from the file's
"company" field unless --company is given.

Exit code: 0 clean, 1 problems found, 2 the file could not be read.
Standard library only. No network access. Python 3.8 or newer.
"""
import argparse
import json
import re
import sys

EM, EN = chr(0x2014), chr(0x2013)  # the em dash and the en dash, by code point so that the source stays readable
DASHES = [(re.compile(EM), "an em dash"), (re.compile(r"(?<!-)--(?!-)"), "a double dash"),
          (re.compile(";"), "a semicolon")]
# An en dash is fine inside a range (years, amounts, months, "2021 to present"), wrong as a sentence dash.
RANGE_TOKEN = r"(\$?\d[\d,.]*[kKmM]?\+?|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|present|now|today)"
EN_DASH = re.compile(r"(\S*)\s?" + EN + r"\s?(\S*)")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
BANNED = re.compile(
    r"\b(delv(e|es|ed|ing)|leverag(e|es|ed|ing)|harness(ed|ing)|(to|can|could|will|would|we|i|they|you) harness|"
    r"unleash(es|ed|ing)?|elevat(e|es|ed|ing)|"
    r"revolutioni[sz](e|es|ed|ing)|reimagin(e|es|ed|ing)|showcas(e|es|ed|ing)|groundbreaking|game[- ]?changers?|"
    r"deep[- ]dives?|paradigms?|synerg(y|ies)|"
    r"foster(s|ed|ing)?(?! (care|home|parents?|child(ren)?|famil(y|ies)|youth))|"
    r"there'?s no denying|it'?s like having|"
    r"think(ing)? outside the box|passionate|thrilled|cutting[- ]edge|seamless(ly)?|spearhead(s|ed|ing)?|pivotal|"
    r"a testament to|the future looks bright|great question|i hope this helps|serves as|boasts?|why it matters)\b",
    re.I)
NOT_ONLY = re.compile(r"\bnot only\b[^.!?]{0,120}\bbut( also)?\b", re.I)
NOT_X_ITS_Y = re.compile(r"\b(it'?s not|that'?s not|this is not|it is not|that is not|this isn'?t|it isn'?t|that isn'?t|"
                         r"it wasn'?t|that wasn'?t)\b (just |only |merely |simply |about )?[^.!?]{1,60}[.,]\s*"
                         r"(it'?s|it is|it was|that'?s|this is|rather)\b", re.I)
NOT_JUST = re.compile(r"\bnot (just|merely|simply)\b[^.!?]{1,80}\bbut\b", re.I)
# the rhetorical sweep 'From startups to hospitals, ...' (not 'From 2020 to 2023' and not 'From what I read, ...')
FROM_TO = re.compile(r"(?:^|[.!?]\s+)From (?!\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:[\w'-]+ ){0,3}[\w'-]+ to "
                     r"(?:[\w'-]+ ){0,4}[\w'-]+\s*[,:]", re.I)
# Wording that apologises for a gap. The fix is to write about the closest real experience or to leave the topic
# out. It is never to claim the missing skill.
INABILITY = re.compile(
    r"\b(i|we) (don'?t|do not|didn'?t|did not) have (any |much |a lot of )?(direct |hands-on |professional |prior |formal |real )?"
    r"(experience|exposure|background|knowledge)|\bno (direct |hands-on |professional |prior |formal |real )?(experience|exposure)\b|"
    r"\bi (haven'?t|have not|never) (used|worked|built|done|had)\b|\bi (don'?t|do not) know\b|\bnot familiar\b|\bunfamiliar\b|"
    r"\blimited (experience|exposure|knowledge)\b|\bi lack\b|\bunfortunately\b|\bi'?m not (experienced|familiar|sure)\b", re.I)
WORDS_MAX = 40  # a sentence longer than this is almost always two sentences joined


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def check_text(where, text, company=""):
    """Problems in one piece of prose, each as 'WHERE: problem'."""
    text = text or ""
    company = (company or "").strip()  # exempt the name only as a whole word: "On" must not cut "passionate"
    probe = re.sub(r"(?<!\w)" + re.escape(company) + r"(?!\w)", " ", text, flags=re.I) if company else text
    out = []
    for rx, what in DASHES:
        m = rx.search(probe)
        if m:
            out.append(f"{where}: {what} ({probe[max(0, m.start() - 25):m.end() + 25].strip()!r}); "
                       f"rewrite as two sentences or use a comma")
    for m in EN_DASH.finditer(probe):
        left, right = m.group(1).strip(".,()"), m.group(2).strip(".,()")
        if not (re.fullmatch(RANGE_TOKEN, left, re.I) and re.fullmatch(RANGE_TOKEN, right, re.I)):
            out.append(f"{where}: an en dash used as a dash ({probe[max(0, m.start() - 25):m.end() + 25].strip()!r}); "
                       f"rewrite as two sentences or use a comma")
            break
    if EMOJI.search(probe):
        out.append(f"{where}: an emoji")
    for m in BANNED.finditer(probe):
        out.append(f"{where}: worn-out word or phrase {m.group(0)!r}")
    for rx, what in ((NOT_ONLY, '"not only X, but also Y"'), (NOT_JUST, '"not just X, but Y"'),
                     (NOT_X_ITS_Y, '"It\'s not X. It\'s Y."'), (FROM_TO, '"From X to Y"')):
        m = rx.search(probe)
        if m:
            out.append(f"{where}: the {what} construction ({m.group(0)[:60]!r})")
    m = INABILITY.search(probe)
    if m:
        out.append(f"{where}: {m.group(0)!r} apologises for a gap; write about the closest real experience or "
                   f"leave the topic out (never claim the missing skill)")
    for s in sentences(probe):
        n = len(s.split())
        if n > WORDS_MAX:
            out.append(f"{where}: a {n}-word sentence ({s[:50]!r}...); split it")
    return out


def check_application(app, company=None):
    """Style problems in the résumé summary and in each paragraph of the cover letter."""
    if not isinstance(app, dict):
        return ["the application file must be a JSON object"]
    company = company if company is not None else app.get("company") if isinstance(app.get("company"), str) else ""
    resume = app.get("resume") if isinstance(app.get("resume"), dict) else {}
    letter = app.get("cover_letter") if isinstance(app.get("cover_letter"), dict) else {}
    out = check_text("resume summary", resume.get("summary") if isinstance(resume.get("summary"), str) else "", company)
    paragraphs = letter.get("paragraphs") if isinstance(letter.get("paragraphs"), list) else []
    for n, p in enumerate(paragraphs, 1):
        out += check_text(f"cover letter paragraph {n}", p if isinstance(p, str) else "", company)
    return out


def _no_duplicate_keys(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} appears twice in one object")
        seen.add(key)
    return dict(pairs)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Optional style check for the summary and the cover letter.",
                                 epilog="Exit code: 0 clean, 1 problems found, 2 the file could not be read.")
    ap.add_argument("tailored", help="the tailored application JSON")
    ap.add_argument("--company", help="employer name to exempt (default: the file's \"company\" field)")
    args = ap.parse_args(argv)
    try:
        with open(args.tailored, encoding="utf-8-sig") as f:
            app = json.load(f, object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, RecursionError) as e:
        print(f"cannot check: cannot read {args.tailored}: {str(e) or type(e).__name__}")
        return 2
    problems = check_application(app, args.company)
    if problems:
        print("\n".join(f"- {p}" for p in problems))
        print(f"{len(problems)} style problem(s). Reword without adding any claim, then run the guard again.")
        return 1
    print("OK: no style problems in the summary or the cover letter. This says nothing about facts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
