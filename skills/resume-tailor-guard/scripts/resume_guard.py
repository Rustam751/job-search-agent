#!/usr/bin/env python3
"""Deterministic no-fabrication guard: a tailored résumé may only use facts that are in the master résumé.

Usage:
  python resume_guard.py MASTER.json                   validate the master résumé on its own
  python resume_guard.py MASTER.json TAILORED.json     check a tailored résumé against the master
  python resume_guard.py MASTER.json TAILORED.json --markdown DIR
                                                       the same and, only if nothing is found, write DIR/resume.md
                                                       (and DIR/cover_letter.md) with the contact block from the master

TAILORED.json is a résumé object, or an application object that holds one under "resume".

Exit code: 0 nothing found, 1 problems found, 2 the check could not run or its output could not be written
(unreadable file, unusable or unconfirmed master, a Markdown file that cannot be written).
With MASTER.json alone, 0 means that the shape is valid. The second line of the output says whether the
candidate has confirmed it. The tailoring check refuses an unconfirmed master.

Checks on the tailored résumé:
  1. Contact block: if one is present, every field equals the master's.
  2. Entry headers: heading, subheading, location, dates and link are copied from ONE master entry.
     A field may be left out. It may not be changed.
  3. Bullets: every bullet cites source_ids, the ids exist in the master, and they belong to that same entry.
  4. Numbers in a bullet: each one appears in the master bullets that the bullet cites.
  5. Numbers in the summary: each one appears somewhere in the master.
  6. Skills: every listed skill is in the master's skills (case-insensitive).
  7. Skill labels and section titles: made of words from the master's own group and section names plus a
     short list of plain words. An entry stays under a title that names the master section it comes from.
  8. Unknown fields are rejected, because nothing would check what they say.

This is a guard, not a guarantee. It compares numbers and copied strings. It cannot judge meaning: a target
stated as a result, a dropped qualifier ("pilot", "on a test set"), a design described as shipped or a wider
scope ("led", "in production") all pass when the numbers match. Words in a bullet or in the summary are not
checked, so a tool or a title named there passes. It does not check the cover letter at all. Those need a
careful reviewer and the candidate's own read.

Standard library only. No network access. Python 3.8 or newer.
"""
import argparse
import datetime
import json
import os
import re
import sys
import unicodedata

HEADER = ("heading", "subheading", "location", "dates", "link")  # copied from a master entry, never rewritten
RESERVED = ("contact", "confirmed_on", "skills", "facts", "rewording_notes")  # master keys that are not sections
NOT_FACTS = ("contact", "confirmed_on", "rewording_notes")  # master keys that never supply a usable number
KEYS = {"resume": {"contact", "summary", "skills", "sections"}, "skills group": {"label", "items"},
        "section": {"title", "entries"}, "entry": set(HEADER) | {"bullets"}, "bullet": {"text", "source_ids"},
        "master entry": set(HEADER) | {"bullets"}, "master bullet": {"id", "text"}}
# Plain words that may stand in a skills label or in a section title next to the master's own words.
LABEL_WORDS = ("and or of for in the with other general core additional technical tools tooling skills languages "
               "frameworks libraries platforms databases technologies methods practices software")
TITLE_WORDS = ("and or of the other selected relevant additional professional technical academic personal work "
               "history recent earlier key")

_FOLD = {c: "-" for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)}  # every dash and minus sign
_FOLD.update({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})  # curly quotes


# ---------- numbers ----------

_SMALL = ("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_UNITS = "one two three four five six seven eight nine".split()
_WORD_VALUE = dict({w: i for i, w in enumerate(_SMALL, 2)}, **{w: 10 * i for i, w in enumerate(_TENS, 2)})
_UNIT_VALUE = {w: i for i, w in enumerate(_UNITS, 1)}
_POWER = {"hundred": 2, "thousand": 3, "lakh": 5, "million": 6, "crore": 7, "billion": 9, "trillion": 12,
          "k": 3, "m": 6, "mm": 6, "mn": 6, "mio": 6, "mln": 6, "mil": 6, "b": 9, "bn": 9, "bln": 9}
_STEM = "hundred|thousand|lakh|million|crore|billion|trillion"
_SCALE = r"(" + _STEM + r")s?"  # one magnitude word, singular or plural
_SHORT = r"(k|m|mn|mio|mln|mil|bn?|bln|(?-i:MM))"  # 5k  $5M  2bn  14 mn; MM only in capitals (5mm is a length)

# A number in digits: 12  0.87  .87  1,200  1 200  1'200  1.200.000  14e6, with an optional magnitude after it:
# 5k  $5M  6 K  5 million  14-thousand  6+ million  14 hundred thousand.
# A unit may follow directly (200ms, 16GB, 10x, 40%) and "x10" is read as 10. Digits glued to the end of a name
# are part of the name, not a number: S3, p95, v2.1, GPT4.
DIGITS = re.compile(
    r"(?:(?<![A-Za-z0-9])x(?=[0-9])|(?<![A-Za-z0-9])(?<![0-9]\.))"
    r"([0-9]{1,3}(?:(?:,[0-9]{3})+|(?: [0-9]{3})+|(?:'[0-9]{3})+)(?![0-9])(?:\.[0-9]+)?"
    r"|[0-9]{1,3}(?:\.[0-9]{3}){2,}(?![0-9])|[0-9]+(?:\.[0-9]+)?|(?<!\.)\.[0-9]+)"
    r"(?:e([+-]?[0-9]{1,2})(?![A-Za-z0-9.]))?"
    r"(?:\+?[\s-]*" + _SCALE + r"(?:[\s-]+" + _SCALE + r")?(?![A-Za-z0-9])"
    r"|" + _SHORT + r"(?![A-Za-z0-9])| " + _SHORT + r"(?![A-Za-z0-9-]))?", re.I)
# A number in words: two ... nineteen, twenty ... ninety, twenty-five, and "one", "a" or "an" in front of a
# magnitude word (a hundred, one million). A bare "one" is left out on purpose: "one of", "no one" and
# "one-on-one" are not quantities.
WORDS = re.compile(
    r"(?<![A-Za-z0-9])(?:(" + "|".join(_TENS) + r")(?:-(" + "|".join(_UNITS) + r"))?|(" + "|".join(_SMALL) + r")"
    r"|(one|an?)(?=[\s-]+(?:" + _STEM + r")))"
    r"(?:[\s-]+" + _SCALE + r"(?:[\s-]+" + _SCALE + r")?)?(?![A-Za-z0-9])", re.I)
# A magnitude word with no number in front of it ("millions of users", "a few thousand", "dozens").
BARE = re.compile(r"(?<![A-Za-z0-9])((?:" + _STEM + r"|dozen)s?)(?![A-Za-z0-9])", re.I)
_EMPHASIS = re.compile(r"[*`]+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")


def _plain(text):
    """Text the way a reader sees it: no emphasis markers, no invisible characters, every kind of digit turned
    into 0-9, every dash into '-' and every run of white space into one space."""
    text = unicodedata.normalize("NFKC", str(text)).translate(_FOLD)
    text = "".join(str(unicodedata.decimal(c)) if c > "9" and c.isdecimal() else c
                   for c in text if unicodedata.category(c) != "Cf")
    return " ".join(_EMPHASIS.sub("", text).split())


def _canonical(digits, power=0):
    """One spelling per value: '1,200' -> '1200', '0.870' -> '0.87', ('1.2', 6) -> '1200000', ('14', -3) -> '0.014'.
    '14.000' is left alone. It may mean fourteen or fourteen thousand, so it only ever matches itself."""
    if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3}){2,}", digits):
        digits = digits.replace(".", "")
    elif power == 0 and re.fullmatch(r"[1-9][0-9]{0,2}\.[0-9]{3}", digits):
        return digits
    whole, _, frac = re.sub(r"[, ']", "", digits).partition(".")
    point, both = len(whole) + power, whole + frac  # where the decimal point lands in the digits
    if point <= 0:
        whole, frac = "0", "0" * -point + both
    else:
        both = both.ljust(point, "0")
        whole, frac = both[:point], both[point:]
    whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _tokens(text):
    """{canonical value: the text it was read from} for every number in a text. A magnitude word that stands
    without a number is a token too, under its own spelling."""
    text, found, spans = _plain(text), {}, []
    for m in DIGITS.finditer(text):
        digits, exponent = m.group(1), int(m.group(2) or 0)
        power = exponent + sum(_POWER[s.lower()] for s in m.groups()[2:] if s)
        found.setdefault(_canonical(digits, power), m.group(0).strip())
        spans.append(m.span())
    for m in WORDS.finditer(text):
        tens, unit, small, _, first, second = ((g or "").lower() for g in m.groups())
        value = _WORD_VALUE[tens] + _UNIT_VALUE.get(unit, 0) if tens else _WORD_VALUE[small] if small else 1
        found.setdefault(_canonical(str(value), sum(_POWER[s] for s in (first, second) if s)), m.group(0))
        spans.append(m.span())
    for m in BARE.finditer(text):
        if not any(a <= m.start() and m.end() <= b for a, b in spans):
            found.setdefault(m.group(1).lower(), m.group(1))
    return found


def numbers(text):
    """The numbers in a text as canonical strings. '1,200', '1200' and '1.2k' are all '1200'; '$5M', '5 million'
    and '5-million' are '5000000'; 'three' is '3'; 'a hundred' is '100'; '10x', '10%' and '10ms' are '10'.
    A magnitude word without a number ('millions') is returned as that word."""
    return set(_tokens(text))


def _extra(text, allowed):
    """The numbers of a text that are not allowed, as they were written: smallest first, words last."""
    tokens = _tokens(text)

    def order(n):
        try:
            return 0, float(n), n
        except ValueError:
            return 1, 0.0, n

    return [tokens[n] for n in sorted(set(tokens) - allowed, key=order)]


# ---------- the master résumé ----------

def norm(text):
    """Key for comparing copied text: case, spacing and the style of dashes and quotes do not count."""
    text = unicodedata.normalize("NFKC", str(text)).translate(_FOLD).casefold()
    return re.sub(r"\s*-\s*", "-", " ".join(text.split()))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _key(field, value):
    """A header field as it is compared: links exactly, everything else through norm()."""
    return value.strip() if field == "link" else norm(value)


def _words(text):
    """The words of a label, a title or a name: 'Cloud & tooling (AWS, C++)' -> ['cloud', 'tooling', 'aws', 'c++']."""
    found = re.findall(r"(?:[^\W_]|[+#.])+", norm(text).replace("_", " "))
    return [w.strip(".") for w in found if w.strip(".")]


def _stem(word):
    """A word without its plural ending, so that 'Project' matches 'projects' and 'library' matches 'libraries'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


LABEL_STEMS = frozenset(_stem(w) for w in LABEL_WORDS.split())
TITLE_STEMS = frozenset(_stem(w) for w in TITLE_WORDS.split())


def confirmed_on(master):
    """The day on which the candidate confirmed the master, or None. It must be a real date written as
    YYYY-MM-DD and must not lie in the future (one day of slack for time zones)."""
    value = master.get("confirmed_on") if isinstance(master, dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        return None
    try:
        day = datetime.date.fromisoformat(value.strip())
    except ValueError:
        return None
    return day if day <= datetime.date.today() + datetime.timedelta(days=1) else None


def sections_of(master):
    """(name, entries) for each list of entries in the master: experience, projects, education and any others."""
    return [(k, v) for k, v in master.items() if k not in RESERVED and not k.startswith("_") and isinstance(v, list)]


def skill_groups(skills):
    """(group name, [skills]) pairs of a skills value: {"Group": [...]}, [...] or [{"label": ..., "items": [...]}]."""
    if isinstance(skills, dict):
        pairs = list(skills.items())
    else:
        pairs = [(g.get("label", ""), g.get("items", [])) if isinstance(g, dict) else ("", g)
                 for g in (skills if isinstance(skills, list) else [])]
    return [(name, items if isinstance(items, list) else [items]) for name, items in pairs]


def skill_items(skills):
    """Every skill in a skills value."""
    return [s for _, items in skill_groups(skills) for s in items]


def fact_strings(master):
    """Every string in the master that states something about the candidate. The contact block, the notes on
    wording, bullet ids and links are left out, so that a phone number never makes '555' a usable figure."""
    out = []

    def walk(value, key=""):
        if isinstance(value, str):
            if key not in ("id", "link") and "://" not in value:
                out.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                if not k.startswith("_"):
                    walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)

    for k, v in master.items():
        if k not in NOT_FACTS and not k.startswith("_"):
            walk(v, k)
    return out


def _unknown(kind, obj, where="", advice="nothing checks them, remove them"):
    extra = sorted(k for k in obj if k not in KEYS[kind] and not (kind.startswith("master") and k.startswith("_")))
    return [f"{where}unknown {kind} field(s) {extra}: {advice}"] if extra else []


def validate_master(master):
    """Problems that make a master résumé unusable as the source of truth; [] means it can be used."""
    if not isinstance(master, dict):
        return ["the master must be a JSON object"]
    out = []
    contact = master.get("contact")
    if not isinstance(contact, dict) or not _text(contact.get("name")):
        out.append('contact must be an object with a "name"')
    elif not all(isinstance(v, str) for v in contact.values()):
        out.append("every contact field must be a string")
    if "confirmed_on" in master and confirmed_on(master) is None:
        out.append("confirmed_on must be the date of the candidate's confirmation, written like 2024-09-02 and "
                   "not in the future; set it only after the candidate confirmed every line")
    skills = master.get("skills", {})
    if not isinstance(skills, (dict, list)) or not all(_text(s) for s in skill_items(skills)) \
            or not all(isinstance(name, str) for name, _ in skill_groups(skills)):
        out.append('skills must be {"Group": ["skill", ...]}, a list of skills or a list of {"label", "items"}')
    for key in ("facts", "rewording_notes"):
        value = master.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            out.append(f"{key} must be a list of strings")
    seen, entries = {}, 0
    for key, value in master.items():
        if key in RESERVED or key.startswith("_"):
            continue
        if not isinstance(value, list):
            out.append(f'unknown field {key!r}: a section is a list of entries, and any other fact goes into "facts"')
            continue
        if not _words(key):
            out.append(f"section name {key!r} needs at least one word")
        for n, e in enumerate(value, 1):
            if not isinstance(e, dict) or not _text(e.get("heading")):
                out.append(f'{key} entry {n}: must be an object with a "heading"')
                continue
            entries += 1
            where = f"{key}: {e['heading'].strip()}"
            out += _unknown("master entry", e, f"{where}: ", "put the detail into a bullet")
            out += [f"{where}: {f} must be a string" for f in HEADER[1:] if not isinstance(e.get(f, ""), str)]
            bullets = e.get("bullets", [])
            if not isinstance(bullets, list):
                out.append(f"{where}: bullets must be a list")
                continue
            for b in bullets:
                if not isinstance(b, dict) or not _text(b.get("id")) or not _text(b.get("text")):
                    out.append(f'{where}: every bullet must be an object with an "id" and a "text"')
                    continue
                out += _unknown("master bullet", b, f"{where}: ", "a bullet has an id and a text, nothing else")
                if b["id"] in seen:
                    out.append(f"bullet id {b['id']!r} is used twice ({seen[b['id']]} and {where})")
                seen[b["id"]] = where
    if not entries:
        out.append("the master has no entries (experience, projects, education ...)")
    return out


def master_index(master):
    """Lookup tables for a valid master: its entries, bullet texts, the entry each bullet belongs to, its skills,
    the pool of every number it contains, the words of its skills and the words of each section name."""
    entries, bullets, owner = [], {}, {}
    for section, items in sections_of(master):
        for e in items:
            entries.append(dict({f: _key(f, e.get(f, "")) for f in HEADER}, raw=e, section=section))
            for b in e.get("bullets", []):
                bullets[b["id"]], owner[b["id"]] = b["text"], len(entries) - 1
    groups = skill_groups(master.get("skills"))
    skills = {norm(s) for _, items in groups for s in items}
    pool = set().union(*(numbers(s) for s in fact_strings(master)))
    skill_words = {_stem(w) for name, items in groups for text in [name] + items for w in _words(text)}
    section_words = {name: {_stem(w) for w in _words(name)} for name, _ in sections_of(master)}
    return entries, bullets, owner, skills, pool, skill_words, section_words


# ---------- the check ----------

def resume_of(doc):
    """The résumé inside an application file, or the document itself when it already is a résumé."""
    return doc["resume"] if isinstance(doc, dict) and "resume" in doc else doc


def _loose(where, text, pool):
    """Free text outside the bullets: its numbers must appear somewhere in the master."""
    if not isinstance(text, str):
        return [f"{where} must be a string"]
    extra = _extra(text, pool)
    return [f"{where}: numbers {extra} are not in the master"] if extra else []


def _list(obj, key, out):
    value = obj.get(key, [])
    if isinstance(value, list):
        return value
    out.append(f"{key} must be a list")
    return []


def _label(label, skill_words):
    """A skills label is printed next to the skills, so it may only use the master's own words for them."""
    if not isinstance(label, str):
        return ["skills label must be a string"]
    odd = sorted({w for w in _words(label) if _stem(w) not in skill_words and _stem(w) not in LABEL_STEMS})
    return [f"skills label {label!r}: {odd} are not words from the master's skill groups or skills; "
            f"label a group with the master's own words"] if odd else []


def _title(title, section_words):
    """(the master sections that a title names, problems). A title is built from the master's section names."""
    if not isinstance(title, str):
        return set(), ["section title must be a string"]
    names, known = sorted(section_words), set().union(*section_words.values())
    stems = {_stem(w) for w in _words(title)}
    named = {name for name, words in section_words.items() if words & stems}
    odd = sorted({w for w in _words(title) if _stem(w) not in known and _stem(w) not in TITLE_STEMS})
    if odd:
        return named, [f"section title {title!r}: {odd} are not in the names of the master's sections {names}"]
    if not named:
        return named, [f"section title {title!r} does not name a master section {names}"]
    return named, []


def _check_entry(e, entries, bullets, owner, title, named):
    if not isinstance(e, dict) or not _text(e.get("heading")):
        return ['every entry must be an object with a "heading" copied from the master']
    name = e["heading"].strip()
    out = _unknown("entry", e, f"{name}: ")
    bad = [f for f in HEADER[1:] if not isinstance(e.get(f, ""), str)]
    if bad or not isinstance(e.get("bullets", []), list):
        return out + [f"{name}: {f} must be a string" for f in bad] + \
            ([] if isinstance(e.get("bullets", []), list) else [f"{name}: bullets must be a list"])
    given = {f: _key(f, e.get(f, "")) for f in HEADER}
    cited = [i for b in e.get("bullets", []) if isinstance(b, dict) and isinstance(b.get("source_ids"), list)
             for i in b["source_ids"] if isinstance(i, str)]

    def changed(m):
        return [f for f in HEADER[1:] if given[f] and given[f] != m[f]]

    # the master entry this one was copied from: same heading, fewest changed fields, most of the cited bullets
    same = [i for i, m in enumerate(entries) if m["heading"] == given["heading"]]
    home = min(same, key=lambda i: (len(changed(entries[i])), -sum(owner.get(c) == i for c in cited)), default=None)
    if home is None:
        out.append(f"{name}: no master entry has this heading (copy heading, subheading, location, dates and "
                   f"link from the master entry)")
    else:
        for f in changed(entries[home]):
            theirs = entries[home]["raw"].get(f, "")
            out.append(f"{name}: {f} {e[f]!r} differs from the master entry "
                       + (f"({theirs!r})" if theirs else f"(which has no {f})"))
        if named and entries[home]["section"] not in named:
            out.append(f"{name}: this entry is in the master's {entries[home]['section']!r} section, but it is "
                       f"shown under {title!r}")
    for b in e.get("bullets", []):
        if not isinstance(b, dict) or not _text(b.get("text")):
            out.append(f'{name}: every bullet must be an object {{"text": ..., "source_ids": [...]}}')
            continue
        out += _unknown("bullet", b, f"{name}: ")
        ids, short = b.get("source_ids"), b["text"][:70]
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            out.append(f"{name}: bullet without source_ids: {short}")
            continue
        missing = [i for i in ids if i not in bullets]
        if missing:
            out.append(f"{name}: bullet cites ids that are not in the master {missing}: {short}")
            continue
        foreign = [i for i in ids if home is not None and owner[i] != home]
        if foreign:
            others = ", ".join(sorted({entries[owner[i]]["raw"]["heading"].strip() for i in foreign}))
            out.append(f"{name}: bullet cites {foreign} from another master entry ({others}): {short}")
        extra = _extra(b["text"], set().union(*(numbers(bullets[i]) for i in ids)))
        if extra:
            out.append(f"{name}: numbers {extra} are not in the cited master bullets {ids}: {short}")
    return out


def check(master, tailored):
    """Problems in a tailored résumé as readable lines; [] means the guard found nothing.
    Raises ValueError when the master itself is unusable."""
    bad = validate_master(master)
    if bad:
        raise ValueError("unusable master: " + "; ".join(bad))
    entries, bullets, owner, skills, pool, skill_words, section_words = master_index(master)
    resume = resume_of(tailored)
    if not isinstance(resume, dict):
        return ["the tailored resume must be a JSON object"]
    out = _unknown("resume", resume)
    contact = resume.get("contact")
    if contact is not None and not isinstance(contact, dict):
        out.append("contact must be an object")
    for k, v in (contact.items() if isinstance(contact, dict) else ()):
        if master["contact"].get(k) != v:
            out.append(f"contact field {k!r} differs from the master")
    out += _loose("summary", resume.get("summary", ""), pool)
    for group in _list(resume, "skills", out):
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            out.append('every skills group must be an object {"label": ..., "items": [...]}')
            continue
        out += _unknown("skills group", group)
        out += _label(group.get("label", ""), skill_words)
        out += [f"skill not in the master skills: {s!r}" for s in group["items"]
                if not isinstance(s, str) or norm(s) not in skills]
    for section in _list(resume, "sections", out):
        if not isinstance(section, dict) or not isinstance(section.get("entries"), list):
            out.append('every section must be an object {"title": ..., "entries": [...]}')
            continue
        out += _unknown("section", section)
        named, problems = _title(section.get("title", ""), section_words)
        out += problems
        for e in section["entries"]:
            out += _check_entry(e, entries, bullets, owner, section.get("title", ""), named)
    return out


# ---------- Markdown (written only from a résumé that passed the check) ----------

_STRUCTURE = re.compile(r"(#{1,6}\s|>|[-+*]\s)|([0-9]{1,9})[.)]\s")


def _line(text):
    """One line of output: a line break inside a field must not turn into Markdown structure."""
    return " ".join(text.split())


def _paragraph(text):
    """A line of running text that cannot open a heading, a list or a quote."""
    line = _line(text)
    m = _STRUCTURE.match(line)
    if not m:
        return line
    return "\\" + line if m.group(1) else m.group(2) + "\\" + line[m.end(2):]


def _letterhead(master):
    contact = master["contact"]
    rest = [_line(v) for k, v in contact.items() if k != "name" and v.strip()]
    return ["# " + _line(contact["name"]), ""] + ([" | ".join(rest), ""] if rest else [])


def resume_markdown(master, resume):
    """A checked résumé as Markdown. The contact block comes from the master, never from the tailored file."""
    lines = _letterhead(master)
    if resume.get("summary", "").strip():
        lines += ["## Summary", "", _paragraph(resume["summary"]), ""]
    groups = [g for g in resume.get("skills", []) if g["items"]]
    if groups:
        lines += ["## Skills", ""]
        for g in groups:
            label = _line(g.get("label", ""))
            lines.append("- " + (f"**{label}:** " if label else "") + ", ".join(_line(s) for s in g["items"]))
        lines.append("")
    for section in resume.get("sections", []):
        lines += ["## " + _line(section.get("title", "")), ""]
        for e in section["entries"]:
            sub, place, dates, link = (_line(e.get(f, "")) for f in HEADER[1:])
            head = f"[{_line(e['heading'])}]({link})" if link else _line(e["heading"])
            lines.append("### " + " | ".join(x for x in (head, place, "" if sub else dates) if x))
            if sub:
                lines.append(" | ".join(x for x in (f"*{sub}*", dates) if x))
            lines.append("")
            if e.get("bullets"):
                lines += ["- " + _line(b["text"]) for b in e["bullets"]] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def letter_markdown(master, letter):
    """The cover letter as Markdown, or None if it is malformed. The guard does not check what it says."""
    if not isinstance(letter, dict) or not all(_text(letter.get(k)) for k in ("date", "greeting", "closing")) \
            or not isinstance(letter.get("paragraphs"), list) or not letter["paragraphs"] \
            or not all(_text(p) for p in letter["paragraphs"]):
        return None
    lines = _letterhead(master) + [_paragraph(letter["date"]), "", _paragraph(letter["greeting"]), ""]
    for p in letter["paragraphs"]:
        lines += [_paragraph(p), ""]
    lines += [_paragraph(letter["closing"]), "", _line(master["contact"]["name"])]
    return "\n".join(lines) + "\n"


# ---------- command line ----------

class Unusable(Exception):
    """The check could not run, or its output could not be written. The message is the line to print."""


def _no_duplicate_keys(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} appears twice in one object")
        seen.add(key)
    return dict(pairs)


def load(path):
    try:
        with open(path, encoding="utf-8-sig") as f:  # -sig: a file saved with a byte-order mark still loads
            return json.load(f, object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, RecursionError) as e:
        raise Unusable(f"cannot check: cannot read {path}: {str(e) or type(e).__name__}")


def run(args):
    master = load(args.master)
    problems = validate_master(master)
    day = confirmed_on(master)
    if args.tailored is None:
        if problems:
            print("master resume problems:\n" + "\n".join(f"- {p}" for p in problems))
            return 1
        entries, bullets, _, skills = master_index(master)[:4]
        print(f"master OK: {len(sections_of(master))} sections, {len(entries)} entries, {len(bullets)} bullets, "
              f"{len(skills)} skills")
        if day:
            print(f"confirmed by the candidate on {day.isoformat()}")
        else:
            print('NOT CONFIRMED YET: show every line to the candidate, apply their corrections, then set '
                  '"confirmed_on" to that date. The guard refuses to check against an unconfirmed master.')
        return 0
    if problems:
        raise Unusable("cannot check: the master resume is not usable:\n" + "\n".join(f"- {p}" for p in problems))
    if day is None:
        raise Unusable('cannot check: the master resume has no "confirmed_on" date. The candidate must confirm '
                       'every line first. Then set "confirmed_on" to that date.')
    tailored = load(args.tailored)
    issues = check(master, tailored)
    files, notes = {}, []
    if args.markdown and not issues:
        files["resume.md"] = resume_markdown(master, resume_of(tailored))
        if isinstance(tailored, dict) and "resume" in tailored and tailored.get("cover_letter") is not None:
            files["cover_letter.md"] = letter_markdown(master, tailored["cover_letter"])
            if files["cover_letter.md"] is None:
                issues = ["cover_letter is malformed (run check_shape.py); no Markdown was written"]
        elif os.path.isfile(os.path.join(args.markdown, "cover_letter.md")):
            notes.append(f"note: {os.path.normpath(os.path.join(args.markdown, 'cover_letter.md'))} is from an "
                         f"earlier run and was not updated, because this file has no cover letter")
    if issues:
        print("\n".join(f"- {p}" for p in issues))
        print(f"{len(issues)} problem(s). Remove or correct each claim in the tailored file. "
              f"Never edit the master to make the guard pass.")
        return 1
    written = []
    try:
        if files:
            os.makedirs(args.markdown, exist_ok=True)
        for name, text in files.items():
            path = os.path.normpath(os.path.join(args.markdown, name))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            written.append(f"wrote {path}")
    except OSError as e:
        raise Unusable(f"not written: the resume passed the guard, but the Markdown could not be written: {e}")
    print("OK: no unsupported numbers, listed skills, labels, entry headers or contact details in the resume.")
    print("Not checked: what the sentences mean, and the cover letter. "
          "Those need the fact-check and the candidate's own read.")
    for line in written + notes:
        print(line)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="No-fabrication guard: check a tailored resume against the master resume.",
        epilog="Exit code: 0 nothing found, 1 problems found, 2 the check could not run or its output could not "
               "be written.")
    ap.add_argument("master", help="the master resume JSON")
    ap.add_argument("tailored", nargs="?", help="the tailored resume or application JSON (omit to validate the master)")
    ap.add_argument("--markdown", metavar="DIR", help="if the check passes, write resume.md and cover_letter.md here")
    args = ap.parse_args(argv)
    if args.markdown and args.tailored is None:
        ap.error("--markdown needs a tailored file")
    try:
        return run(args)
    except Unusable as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
