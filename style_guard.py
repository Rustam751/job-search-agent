"""Deterministic half of the candidate's writing rules (private/writing_rules.md; the fact-checker handles the rest).

check_text(where, text)            -> problems in one piece of prose (cover-letter paragraph, free-text answer, summary)
check_field(question, answer, ...) -> problems in a short form answer (a bare "No" to a skills question, ...)

Usage: python style_guard.py "some text"      (prints the problems, exit 1 if any)
"""
import re
import sys

DASHES = [(re.compile(r"—"), "an em dash"), (re.compile(r"(?<!-)--(?!-)"), "a double dash"), (re.compile(r";"), "a semicolon")]
# An en dash is fine inside a range ('2019–2023', '$140k–$150k', 'Jan–Mar', '2021–present'), wrong as a sentence dash.
RANGE_TOKEN = r"(\$?\d[\d,.]*[kKmM]?\+?|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|present|now|today)"
EN_DASH = re.compile(r"(\S*)\s?–\s?(\S*)")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
BANNED = re.compile(
    r"\b(delv(e|es|ed|ing)|leverag(e|es|ed|ing)|harness(ed|ing)|(to|can|could|will|would|we|i|they|you) harness|"
    r"unleash(es|ed|ing)?|elevat(e|es|ed|ing)|"
    r"revolutioni[sz](e|es|ed|ing)|reimagin(e|es|ed|ing)|showcas(e|es|ed|ing)|groundbreaking|game[- ]?changers?|"
    r"deep[- ]dives?|paradigms?|synerg(y|ies)|foster(s|ed|ing)?(?! city)|there'?s no denying|it'?s like having|"
    r"think(ing)? outside the box|passionate|thrilled|cutting[- ]edge|seamless(ly)?|spearhead(s|ed|ing)?|pivotal|"
    r"a testament to|the future looks bright|great question|i hope this helps|serves as|boasts?|why it matters)\b", re.I)
NOT_ONLY = re.compile(r"\bnot only\b[^.!?]{0,120}\bbut( also)?\b", re.I)
NOT_X_ITS_Y = re.compile(r"\b(it'?s not|that'?s not|this is not|it is not|that is not|this isn'?t|it isn'?t|that isn'?t|"
                         r"it wasn'?t|that wasn'?t)\b (just |only |merely |simply |about )?[^.!?]{1,60}[.,]\s*"
                         r"(it'?s|it is|it was|that'?s|this is|rather)\b", re.I)
NOT_JUST = re.compile(r"\bnot (just|merely|simply)\b[^.!?]{1,80}\bbut\b", re.I)
# the rhetorical sweep 'From startups to hospitals, ...' (not 'From 2016 to 2018' and not 'From what I read, ...')
FROM_TO = re.compile(r"(?:^|[.!?]\s+)From (?!\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:[\w'-]+ ){0,3}[\w'-]+ to "
                     r"(?:[\w'-]+ ){0,4}[\w'-]+\s*[,:]", re.I)
# Phrases that read as inability (writing_rules.md: name the closest real experience instead)
INABILITY = re.compile(
    r"\b(i|we) (don'?t|do not|didn'?t|did not) have (any |much |a lot of )?(direct |hands-on |professional |prior |formal |real )?"
    r"(experience|exposure|background|knowledge)|\bno (direct |hands-on |professional |prior |formal |real )?(experience|exposure)\b|"
    r"\bi (haven'?t|have not|never) (used|worked|built|done|had)\b|\bi (don'?t|do not) know\b|\bnot familiar\b|\bunfamiliar\b|"
    r"\blimited (experience|exposure|knowledge)\b|\bi lack\b|\bunfortunately\b|\bi'?m not (experienced|familiar|sure)\b", re.I)
# A skills / experience question (a bare "No" to one of these reads as inability)
EXPERIENCE_Q = re.compile(
    r"\b(experience|familiar|familiarity|proficien\w*|comfortable|hands-on|knowledge of|skilled|expertise|exposure)\b|"
    r"\bhave you (ever )?(used|built|worked with|deployed|trained|developed|shipped|written|designed|implemented)\b|"
    r"\bdo you (know|use|have (any )?(experience|knowledge|exposure))\b|\byears of\b", re.I)
# ... but not an employment-history, eligibility, degree or logistics question, where a true "No" is simply the answer
NOT_SKILL_Q = re.compile(
    r"previously (worked|employed|applied)|ever (worked|been employed) (for|at|with)|worked (for|at|with) (us|this company|our)|"
    r"former employee|current employee|employed (by|at|with) (us|this company)|sponsor|authori[sz]ed|"
    r"relatives?|family members?|know (anyone|someone|any)|referr|"
    r"non-?compete|terminat|convict|criminal|security clearance|citizen|visa|relocat|travel|on-?site|in-?office|"
    r"hybrid|commute|drug (test|screen)|background (check|investigation|screen)|"
    r"\bph\.?d|doctora|master'?s|bachelor'?s|\bdegree\b|years of (professional|industry|full-?time|post-?grad\w*|paid|work)|"
    r"after graduat|hours|schedule|on-?call|time ?zone|shift|weekend|salary|compensation|\bour (product|api|platform|app|tools?)\b",
    re.I)
WORDS_MAX = 40  # a sentence longer than this is almost always two sentences joined


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def check_text(where, text, company="", inability=True):
    """Problems in one piece of prose, each as 'WHERE: problem'. inability=False for eligibility questions, where
    'No, I have not worked here before' is simply true."""
    text = text or ""
    probe = re.sub(re.escape(company), " ", text, flags=re.I) if company else text  # a company name may contain a banned word
    out = []
    for rx, what in DASHES:
        m = rx.search(probe)
        if m:
            out.append(f"{where}: {what} ({probe[max(0, m.start() - 25):m.end() + 25].strip()!r}); rewrite as two sentences or use a comma")
    for m in EN_DASH.finditer(probe):
        left, right = m.group(1).strip(".,()"), m.group(2).strip(".,()")
        if not (re.fullmatch(RANGE_TOKEN, left, re.I) and re.fullmatch(RANGE_TOKEN, right, re.I)):
            out.append(f"{where}: an en dash used as a dash ({probe[max(0, m.start() - 25):m.end() + 25].strip()!r}); "
                       f"rewrite as two sentences or use a comma")
            break
    if EMOJI.search(probe):
        out.append(f"{where}: an emoji")
    for m in BANNED.finditer(probe):
        out.append(f"{where}: banned word or phrase {m.group(0)!r}")
    for rx, what in ((NOT_ONLY, '"not only X, but also Y"'), (NOT_JUST, '"not just X, but Y"'),
                     (NOT_X_ITS_Y, '"It\'s not X. It\'s Y."'), (FROM_TO, '"From X to Y"')):
        m = rx.search(probe)
        if m:
            out.append(f"{where}: the {what} construction ({m.group(0)[:60]!r})")
    m = INABILITY.search(probe) if inability else None
    if m:
        out.append(f"{where}: {m.group(0)!r} reads as inability; name the closest real experience instead (writing_rules.md)")
    for s in sentences(probe):
        n = len(s.split())
        if n > WORDS_MAX:
            out.append(f"{where}: a {n}-word sentence ({s[:50]!r}...); split it")
    return out


def is_skill_question(question):
    return bool(EXPERIENCE_Q.search(question or "")) and not NOT_SKILL_Q.search(question or "")


def check_field(question, answer, qtype="text", company="", options=()):
    """Problems in one form answer. An option copied from the form is the employer's text, so it only gets the
    skills-question rule; so do short structured answers (names, links, Yes/No)."""
    a = (answer or "").strip()
    out = []
    if re.fullmatch(r"no\.?", a, re.I) and is_skill_question(question):
        out.append(f"{question[:60]}: a bare 'No' to a skills question; answer Yes if the master résumé has the skill or a "
                   f"close equivalent, else mark it needs_user for Russ (writing_rules.md)")
    if options and a in options:
        return out
    if qtype in ("select", "multi_select", "boolean") and options:
        return out  # not one of the options: the re-check reports that
    if qtype == "long_text" or len(a.split()) > 3:  # names, links and places are shorter
        out += check_text(question[:60], a, company, inability=not NOT_SKILL_Q.search(question))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    problems = check_text("text", " ".join(sys.argv[1:]))
    print("\n".join(problems) or "ok")
    sys.exit(1 if problems else 0)
