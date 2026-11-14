#!/usr/bin/env python3
"""Shape and length check for a tailored application file (résumé plus cover letter).

Usage: python check_shape.py TAILORED.json [--final] [--resume-only]
                                           [--letter-words MIN MAX] [--max-resume-words N]

  --final               also require the recorded fact-check and a clean re-check (the last step of the skill).
                        It only reads what is recorded. It cannot tell whether the text changed afterwards.
  --resume-only         the candidate asked for no cover letter
  --letter-words A B    hard limits for the cover letter in words (default 230 400; the target is 280-340)
  --max-resume-words N  fail when the résumé body is longer than N words (default: only report the count)

Exit code: 0 OK, 1 problems found, 2 the file could not be read.

This script knows nothing about facts and never opens the master résumé. Run resume_guard.py for that.
Standard library only. No network access. Python 3.8 or newer.
"""
import argparse
import json
import sys

SEVERITIES = ("fabrication", "overclaim", "rule_violation", "keyword_gap", "style", "length")
LETTER_HARD, LETTER_TARGET = (230, 400), (280, 340)
ONE_PAGE = 580  # a one-page résumé holds roughly 480-580 words of body text
ENTRY_TEXT = ("heading", "subheading", "location", "dates", "link")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def resume_errors(resume):
    if not isinstance(resume, dict):
        return ["resume must be an object"]
    errs = []
    if not _text(resume.get("summary")):
        errs.append("resume.summary is empty")
    skills = resume.get("skills", [])
    if not isinstance(skills, list) or not all(
            isinstance(g, dict) and _text(g.get("label")) and isinstance(g.get("items"), list) and g["items"]
            and all(_text(s) for s in g["items"]) for g in skills):
        errs.append('resume.skills must be a list of {"label": "...", "items": ["...", ...]} groups')
    sections = resume.get("sections")
    if not isinstance(sections, list) or not sections:
        return errs + ["resume.sections is empty"]
    for section in sections:
        if not isinstance(section, dict) or not _text(section.get("title")) \
                or not isinstance(section.get("entries"), list) or not section["entries"]:
            errs.append('every resume section needs a "title" and a non-empty "entries" list')
            continue
        for e in section["entries"]:
            if not isinstance(e, dict) or not _text(e.get("heading")):
                errs.append(f'{section["title"]}: every entry needs a "heading"')
                continue
            name = e["heading"].strip()
            errs += [f"{name}: {f} must be a string" for f in ENTRY_TEXT[1:] if not isinstance(e.get(f, ""), str)]
            bullets = e.get("bullets", [])
            if not isinstance(bullets, list):
                errs.append(f"{name}: bullets must be a list")
                continue
            for b in bullets:
                ids = b.get("source_ids") if isinstance(b, dict) else None
                if not isinstance(b, dict) or not _text(b.get("text")) or not isinstance(ids, list) or not ids \
                        or not all(_text(i) for i in ids):
                    errs.append(f'{name}: every bullet needs a "text" and a non-empty "source_ids" list')
                    break
    return errs


def letter_errors(letter, lo, hi):
    if not isinstance(letter, dict):
        return ["cover_letter must be an object (pass --resume-only if the candidate wants no letter)"]
    errs = [f"cover_letter.{k} is empty" for k in ("date", "greeting", "closing") if not _text(letter.get(k))]
    paragraphs = letter.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs or not all(_text(p) for p in paragraphs):
        return errs + ["cover_letter.paragraphs must be a non-empty list of paragraph strings"]
    words = letter_words(letter)
    if not lo <= words <= hi:
        errs.append(f"cover letter is {words} words (allowed {lo}-{hi}, aim for "
                    f"{LETTER_TARGET[0]}-{LETTER_TARGET[1]})")
    return errs


def finding_errors(where, findings):
    errs = []
    for n, f in enumerate(findings, 1):
        if not isinstance(f, dict) or f.get("severity") not in SEVERITIES \
                or not all(_text(f.get(k)) for k in ("where", "problem")) or not isinstance(f.get("fix"), str):
            errs.append(f'{where} finding {n}: needs "severity" (one of {", ".join(SEVERITIES)}), "where", '
                        f'"problem" and "fix"')
    return errs


def review_errors(app, final):
    """The recorded fact-check and re-check: validated when present, required when final."""
    errs = []
    fact_check, recheck = app.get("fact_check"), app.get("recheck")
    if fact_check is not None or final:
        if not isinstance(fact_check, dict) or not isinstance(fact_check.get("findings"), list):
            errs.append('fact_check is missing: the adversarial fact-check must run and be recorded as '
                        '{"findings": [...], "keyword_coverage": "...", "length_verdict": "..."}')
        else:
            errs += finding_errors("fact_check", fact_check["findings"])
            errs += [f"fact_check.{k} is missing: write one sentence" for k in ("keyword_coverage", "length_verdict")
                     if not _text(fact_check.get(k))]
    if recheck is not None or final:
        if not isinstance(recheck, dict) or not isinstance(recheck.get("clean"), bool) \
                or not isinstance(recheck.get("findings"), list):
            errs.append('recheck is missing: record the fresh re-check as {"clean": true|false, "findings": [...]}')
        else:
            errs += finding_errors("recheck", recheck["findings"])
            if recheck["clean"] and recheck["findings"]:
                errs.append("recheck says clean but lists findings")
            if final and not recheck["clean"]:
                errs.append("the re-check is not clean: repair its findings, then re-check again")
    return errs


def _count(*texts):
    return sum(len(t.replace("**", "").split()) for t in texts if isinstance(t, str))


def resume_words(resume):
    """Words in the résumé body: summary, skills, section titles, entry headers and bullets."""
    if not isinstance(resume, dict):
        return 0
    words = _count(resume.get("summary"))
    for g in resume.get("skills") if isinstance(resume.get("skills"), list) else []:
        if isinstance(g, dict):
            words += _count(g.get("label"), *(g.get("items") if isinstance(g.get("items"), list) else []))
    for section in resume.get("sections") if isinstance(resume.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        words += _count(section.get("title"))
        for e in section.get("entries") if isinstance(section.get("entries"), list) else []:
            if isinstance(e, dict):
                words += _count(*(e.get(f) for f in ENTRY_TEXT[:4]))
                words += _count(*(b.get("text") for b in e.get("bullets", []) if isinstance(b, dict))
                                if isinstance(e.get("bullets", []), list) else ())
    return words


def letter_words(letter):
    return _count(*letter.get("paragraphs", [])) if isinstance(letter, dict) and \
        isinstance(letter.get("paragraphs"), list) else 0


def check(app, final=False, resume_only=False, letter_range=LETTER_HARD, max_resume_words=None):
    """(errors, notes) for an application object. Errors fail the check; notes are advice."""
    if not isinstance(app, dict):
        return ["the application file must be a JSON object"], []
    errs = [f"{k} is empty" for k in ("company", "role") if not _text(app.get(k))]
    if "notes" in app and not isinstance(app["notes"], str):
        errs.append("notes must be a string")
    errs += resume_errors(app.get("resume"))
    letter = app.get("cover_letter")
    if letter is not None or not resume_only:
        errs += letter_errors(letter, *letter_range)
    errs += review_errors(app, final)
    notes, body, words = [], resume_words(app.get("resume")), letter_words(letter)
    if max_resume_words is not None and body > max_resume_words:
        errs.append(f"resume body is {body} words (limit {max_resume_words})")
    elif max_resume_words is None and body > ONE_PAGE:
        notes.append(f"note: the resume body is {body} words; one page holds roughly 480-{ONE_PAGE}")
    if letter_range[0] <= words <= letter_range[1] and not LETTER_TARGET[0] <= words <= LETTER_TARGET[1]:
        notes.append(f"note: the cover letter is {words} words; aim for {LETTER_TARGET[0]}-{LETTER_TARGET[1]}")
    return errs, notes


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
    ap = argparse.ArgumentParser(description="Shape and length check for a tailored application file.",
                                 epilog="Exit code: 0 OK, 1 problems found, 2 the file could not be read.")
    ap.add_argument("tailored", help="the tailored application JSON")
    ap.add_argument("--final", action="store_true", help="also require the recorded fact-check and a clean re-check")
    ap.add_argument("--resume-only", action="store_true", help="the candidate asked for no cover letter")
    ap.add_argument("--letter-words", nargs=2, type=int, metavar=("MIN", "MAX"), default=list(LETTER_HARD))
    ap.add_argument("--max-resume-words", type=int, metavar="N")
    args = ap.parse_args(argv)
    try:
        with open(args.tailored, encoding="utf-8-sig") as f:  # -sig: a byte-order mark does not break the load
            app = json.load(f, object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, RecursionError) as e:
        print(f"cannot check: cannot read {args.tailored}: {str(e) or type(e).__name__}")
        return 2
    errs, notes = check(app, args.final, args.resume_only, tuple(args.letter_words), args.max_resume_words)
    if errs:
        print("\n".join(f"- {e}" for e in errs))
        print(f"{len(errs)} problem(s) with the shape or length of the file.")
        return 1
    letter = app.get("cover_letter")
    print(f"OK: resume body {resume_words(app['resume'])} words"
          + (f", cover letter {letter_words(letter)} words" if letter is not None else ", no cover letter")
          + (", fact-check recorded and re-check clean" if args.final else ""))
    print("\n".join(notes + ["This is a shape check only. Facts are checked by resume_guard.py."]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
