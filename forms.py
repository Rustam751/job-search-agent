"""Fetch the real application form for a posting, so answers are written for the questions actually asked.

Usage: python forms.py <ats> <slug> <job_id> [url]      (prints the normalized form as JSON)

Supported: greenhouse (public boards API), ashby (the public job-board GraphQL endpoint), lever (apply page HTML).
Workday and SmartRecruiters forms sit behind an account or a multi-step flow, so they return [].

Each question: {"label", "required", "type", "options", "description"} where type is one of
text | long_text | select | multi_select | file | boolean | number | education.
flags() marks questions that the rules say must go to Russ or disqualify the posting.
"""
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

from fetch_jobs import get_json, strip_html

GH_TYPES = {"input_text": "text", "textarea": "long_text", "input_file": "file", "input_hidden": "hidden",
            "multi_value_single_select": "select", "multi_value_multi_select": "multi_select"}
ASHBY_TYPES = {"String": "text", "Email": "text", "Phone": "text", "LongText": "long_text", "File": "file",
               "ValueSelect": "select", "MultiValueSelect": "multi_select", "Boolean": "boolean", "Number": "number",
               "Date": "text", "Location": "text", "Score": "select"}
ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    applicationForm { sections { title fieldEntries { ... on FormFieldEntry { isRequired descriptionHtml field } } } }
  }
}"""

def _private_config():
    """The candidate's own settings (git-ignored): his work-authorization policy, status wording and extra terms."""
    try:
        return json.loads((Path(__file__).resolve().parent / "private" / "config.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


PRIVATE_CONFIG = _private_config()

# ---- question classification (always on the LABEL: descriptions are full of legal boilerplate) ----
# Questions only Russ answers: application_answers.md does not cover them.
ASK_RUSS = re.compile(
    r"background (check|investigation|screen|verification)|background (and|&) reference|drug (test|screen)|"
    r"pre-employment|criminal|convict|felony|misdemeanor|\barrest|pending (criminal )?charges|pleaded (guilty|no contest)|"
    r"incarcerat|imprison|\bjail\b|\bprison\b|\bsentenced\b|"
    r"salary history|pay history|current (salary|compensation|base|total comp)|previous (salary|compensation|base)|"
    r"prior (salary|compensation)|most recent (base )?(salary|pay|compensation)|salary (at|in) your (last|previous|current)|"
    r"citizenship|citizen of|countries? of which you are|nationality|national of|place of birth|where were you born|"
    r"country of birth|export[- ]control|deemed export|sanction|f-1 (visa|student|status)|passport|"
    r"relatives? (employed|working|who work|at )|related to (any|an) (employee|current)|"
    r"family members? (who )?(work|employed)|know anyone (who )?(currently )?works|"
    r"non-?compete|non-?solicit|restrictive (covenant|agreement)|agreement that would (prevent|restrict)|"
    r"contractual obligation|(been|ever been) (terminated|dismissed|fired)|asked to resign|"
    r"\breferences\b|\breference (1|2|3|one|two|three|name|email|phone|contact)|referee|"
    r"contact your (references|current employer|previous employer)|"
    r"\bi-?9\b|social security|\bssn\b|date of birth|driver'?s licen", re.I)
ASK_RUSS_CS = re.compile(r"\bOPT\b|\bCPT\b|\bF-1\b")  # case-sensitive: 'opt in' and the F1 score are not visa terms
COUNTRY_QUESTION = re.compile(r"countr(y|ies)|citizen of|nationality|national of|place of birth|where were you born|"
                              r"export[- ]control|deemed export|sanction", re.I)
# Documents Russ has approved for upload (private/application_answers.md): the B.S. transcript only.
APPROVED_DOCS = re.compile(r"undergraduate|bachelor|\(bs\)|\bb\.?s\.? transcript", re.I)
STANDARD_UPLOADS = re.compile(r"resume|résumé|\bcv\b|cover letter", re.I)
# Required one-way / recorded video or AI interviews at the application stage disqualify the posting ...
VIDEO_REQUEST = re.compile(
    r"\b(record|upload|submit|share|send|provide|attach|link to|include)\b[^.?]{0,40}\bvideo\b|"
    r"one-way (video|interview)|async(hronous)? (video|interview)|pre-recorded (video|interview)|"
    r"interview (conducted|led) by an ai|(complete|take)[^.?]{0,30}\bai(-led|-powered)? interview|automated interview|"
    r"hirevue|spark hire|\bwillo\b|myinterview|vidcruiter|hireflix", re.I)
# ... but consent to AI note-taking in real interviews is fine (he consented).
VIDEO_CONSENT = re.compile(r"note-?tak|notetaker|transcri|consent|recording of (your )?interviews", re.I)
# A standing answer never carries over to a question about wrongdoing: 'Yes' to consent is not 'Yes' to a conviction.
NEGATIVE = re.compile(r"convict|criminal|felon|misdemeanor|\bfailed\b|\brefused\b|terminat|dismissed|\bfired\b|violat|"
                      r"disciplin|\bpositive\b|\bcharged\b|\bcharges\b|\bcrimes?\b|\barrest|\bguilty\b|\bpleas?\b|"
                      r"\bpleaded\b|\boffen[cs]es?\b|\bcourts?\b|discharged|\blet go\b|\bresign|incarcerat|imprison|"
                      r"\bjail\b|\bprison\b|\bsentenced\b", re.I)
# Criminal history is never answered by a pattern, except by the entry that holds his own criminal-history answer.
CRIMINAL = re.compile(r"convict|criminal|felon|misdemeanor|\bcharged\b|\bcharges\b|\bcrimes?\b|\barrest|\bguilty\b|"
                      r"\bpleas?\b|\bpleaded\b|\boffen[cs]es?\b|\bcourts?\b|probation|parole|incarcerat|imprison|"
                      r"\bjail\b|\bprison\b|\bsentenced\b|law enforcement|"
                      r"discharged from the (military|armed)|dishonorabl|tested positive|failed a drug", re.I)

# ---- work authorization ----
# Words that name an immigration status. Candidate-specific extras come from private/config.local.json.
STATUS_WORDS = re.compile(r"citizen|green ?card|\bgc\b|permanent resident|\blpr\b|u\.?s\.? persons?|\bead\b|h-?1b|"
                          r"\bf-1\b|visa (type|status|holder)|immigration status|naturali[sz]ed|asylee|refugee|"
                          r"resident alien|(?-i:\bOPT\b)"  # OPT only in capitals ('opt in')
                          + "".join(f"|{t}" for t in PRIVATE_CONFIG.get("extra_status_terms", [])), re.I)
# The text typed into a free-text status box (the candidate's own wording is in private/config.local.json).
OTHER_STATUS = PRIVATE_CONFIG.get("other_status_text") or "Other (employment authorized, no sponsorship required)"
# How each category of work-authorization question is answered: 'yes' | 'no' | 'other' | 'ask'. The candidate's policy is
# in private/config.local.json ("auth_policy"); without it every such question goes to the human.
AUTH_POLICY = PRIVATE_CONFIG.get("auth_policy", {})
STOPWORDS = {"a", "an", "the", "your", "you", "do", "does", "are", "is", "of", "to", "in", "for", "please", "what",
             "with", "and", "or", "our", "any", "this", "that", "be", "will", "would", "if", "i", "we", "us", "at", "on"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def same_question(a, b):
    """Same question: equal normalised labels, or labels that differ only in stopwords."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or (set(na.split()) - STOPWORDS) == (set(nb.split()) - STOPWORDS)


def auth_category(q):
    """The category of a work-authorization question, or None if it is not one. Classification only: how each category
    is answered is the candidate's policy (AUTH_POLICY).

    'authorized'       authorized to work / able to work without sponsorship
    'sponsorship'      will you need sponsorship
    'status_yes_no'    a yes/no question about one specific status (citizen? green card? U.S. person?)
    'visa_type'        a yes/no question about a visa or work-permit document (OPT, EAD, F-1, H-1B)
    'status_select'    pick-your-status, where the form offers an 'Other / authorized' option or a text box
    'no_valid_option'  pick-your-status without such an option
    'mixed'            wording that combines several of these
    """
    raw = q["label"]
    label = raw.lower()
    authorized = re.search(r"authori[sz]ed|eligible to work|legally (able|permitted|allowed)|right to work|work lawfully", label)
    status = STATUS_WORDS.search(raw) or re.search(r"\bstatus\b|eligibility", label)
    if re.search(r"without (the )?(need (for|of) )?(any )?(\w+ )?(visa )?sponsor", label):
        return "authorized"
    if "sponsor" in label:
        return "mixed" if authorized and status else "sponsorship"
    if authorized and not STATUS_WORDS.search(raw):
        return "authorized"
    if not status:
        return None
    yes_no = q["type"] == "boolean" or (q["options"] and {o.lower() for o in q["options"]} <= {"yes", "no"}) or \
        re.match(r"(are|do|have|is|were) you", label)
    if yes_no:
        if authorized:
            return "mixed"
        return "visa_type" if re.search(r"\bOPT\b|\bEAD\b|F-?1\b|[Vv]isa|H-?1B|[Ww]ork permit", raw) else "status_yes_no"
    if q["options"] and not any(acceptable_status_option(o) for o in q["options"]):
        return "no_valid_option"
    return "status_select"


def auth_kind(q):
    """How the candidate's policy answers a work-authorization question: 'yes' | 'no' | 'other' | 'ask', or None if it
    is not one. Categories without a policy entry (and 'mixed', 'no_valid_option') always go to the human."""
    cat = auth_category(q)
    if cat is None:
        return None
    if cat in ("mixed", "no_valid_option"):
        return "ask"
    kind = AUTH_POLICY.get(cat, "ask")
    return kind if kind in ("yes", "no", "other", "ask") else "ask"


def acceptable_status_option(text):
    t = (text or "").strip()
    if t == OTHER_STATUS:
        return True
    return bool(re.search(r"\bother\b|eligible|authori[sz]ed", t, re.I)) and not STATUS_WORDS.search(t)


def auth_answer_problem(kind, answer):
    """None if the answer matches the configured answer for this kind of question."""
    a = (answer or "").strip()
    if a != OTHER_STATUS and STATUS_WORDS.search(a):
        return f"answer {a!r} names a specific status"
    low = a.lower()
    means_no = low.startswith("no") or re.search(r"\b(do not|don't|will not|won't|not)\b (require|need)", low)
    means_yes = low.startswith("yes") or (re.search(r"\bauthori[sz]ed\b", low) and not re.search(r"\bnot\b", low))
    if kind == "yes" and not (means_yes and not means_no):
        return "must be answered Yes (the configured answer)"
    if kind == "no" and not means_no:
        return "must be answered No (the configured answer)"
    if kind == "other" and not acceptable_status_option(a):
        return f"status must be an 'Other / employment authorized' option or the configured status text"
    return None


def pattern_applies(pattern, label, negative_ok=False, criminal_ok=False):
    """True if a standing answer's regex may answer this label.

    Never on a label that asks two questions. Never on a criminal-history label, except for the entry that holds his
    own criminal-history answer (criminal_ok). Never on a wrongdoing label unless the entry is negative_ok AND the only
    wrongdoing words are inside the span the pattern matched (so 'ever terminated? No' does not answer
    'terminated or convicted?')."""
    if not label or label.count("?") > 1 or (CRIMINAL.search(label) and not criminal_ok):
        return False
    try:
        m = re.search(pattern, label, re.I)
    except re.error:
        return False
    if not m:
        return False
    if NEGATIVE.search(label):
        rest = label[:m.start()] + " " + label[m.end():]
        return bool(negative_ok) and not NEGATIVE.search(rest)
    return True


def standing_match(label, standing):
    """The standing answer Russ gave for this exact question, or else the most specific entry whose 'match' regex may
    answer it (see pattern_applies). An exact label always wins."""
    for s in standing:
        if s.get("label") and same_question(label, s["label"]):
            return s
    hits = [s for s in standing if s.get("match") and
            pattern_applies(s["match"], label, s.get("negative_ok"), s.get("criminal_ok"))]
    return max(hits, key=lambda s: len(s["match"])) if hits else None


def standing_fits(q, s):
    """A standing answer may only be used where it is a valid answer: a select must offer it, and a work-authorization
    question must get the answer application_answers.md prescribes."""
    if q.get("options") and not any(standing_answer_ok(o, s["answer"], q["options"]) for o in q["options"]):
        return False
    kind = auth_kind(q)
    if kind == "ask":  # a status question only he decides: his answer to that very question, or an entry he marked
        # status_ok whose answer is literally one of this form's options ('Can work for any employer'), never free text
        exact = bool(s.get("label")) and same_question(q["label"], s["label"])
        return exact or bool(s.get("status_ok") and q.get("options"))
    if kind == "other" and re.search(r"countr(y|ies)|nationality", q["label"], re.I):
        return True  # 'list your countries of citizenship' names countries, not an immigration status
    if kind in ("yes", "no", "other"):
        return auth_answer_problem(kind, s["answer"]) is None
    return True


def standing_answer_ok(given, standing_answer, options=()):
    """The planned answer enters the standing answer: exactly, or as the form's own spelling of it for a select
    ('United States' -> 'United States of America')."""
    g, s = norm(given), norm(standing_answer)
    if g == s:
        return True
    gw, sw = g.split(), s.split()
    same_start = bool(gw) and bool(sw) and gw[0] == sw[0] and (set(sw) <= set(gw) or set(gw) <= set(sw))
    return bool(options) and given in options and same_start and \
        not any(norm(o) == s for o in options)  # the exact option exists: then it must be the one chosen


def greenhouse(slug, job_id):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?questions=true")
    out = []
    groups = [("", d.get("questions") or []), ("", d.get("location_questions") or [])]
    for block in d.get("compliance") or []:
        groups.append(("EEO: ", block.get("questions") or []))
    for prefix, qs in groups:
        for q in qs:
            fields = q.get("fields") or []
            types = [GH_TYPES.get(f.get("type"), f.get("type")) for f in fields]
            if types and all(t == "hidden" for t in types):
                continue
            kind = "file" if "file" in types else (types[0] if types else "text")
            opts = [v.get("label") for f in fields for v in f.get("values") or []]
            out.append(dict(label=prefix + (q.get("label") or ""), required=bool(q.get("required")), type=kind,
                            options=opts, description=strip_html(q.get("description") or "")))
    if d.get("education") in ("education_required", "education_optional"):
        out.append(dict(label="Education (School, Degree, Start year, End year)", required=d["education"] == "education_required",
                        type="education", options=[], description="Greenhouse education block; School/Degree are searchable dropdowns"))
    for q in (d.get("demographic_questions") or {}).get("questions") or []:
        out.append(dict(label="EEO: " + (q.get("name") or q.get("label") or ""), required=bool(q.get("required")),
                        type="multi_select" if q.get("type") == "multi_value_multi_select" else "select",
                        options=[a.get("label") for a in q.get("answer_options") or []], description=""))
    return out


def ashby(slug, job_id):
    body = {"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
            "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": job_id}}
    d = get_json("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", body)
    post = (d.get("data") or {}).get("jobPosting") or {}
    out = []
    for sec in (post.get("applicationForm") or {}).get("sections") or []:
        for fe in sec.get("fieldEntries") or []:
            f = fe.get("field") or {}
            out.append(dict(label=f.get("title", ""), required=bool(fe.get("isRequired")),
                            type=ASHBY_TYPES.get(f.get("type"), str(f.get("type", "")).lower()),
                            options=[o.get("label") for o in f.get("selectableValues") or []],
                            description=strip_html(fe.get("descriptionHtml") or "")))
    return out


def lever(url):
    req = urllib.request.Request(url.rstrip("/") + "/apply", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        page = r.read().decode("utf-8", "replace")
    out = [dict(label="Resume/CV", required=True, type="file", options=[], description=""),
           dict(label="Additional information (cover letter)", required=False, type="long_text", options=[], description="")]
    # A custom question runs until the next question starts (its options are nested <li>s, so '</li>' is no boundary).
    starts = [m.start() for m in re.finditer(r'<li class="application-question[^"]*custom-question', page)]
    end = page.find('class="section eeo-section', starts[-1]) if starts else -1
    bounds = starts + [end if end > 0 else len(page)]
    kinds = {"textarea": "long_text", "multiple-choice": "select", "dropdown": "select", "multiple-select": "multi_select",
             "file": "file", "text": "text"}
    for s, e in zip(bounds, bounds[1:]):
        block = page[s:e]
        cls = (re.search(r'class="application-label([^"]*)"', block) or [None, ""])[1]
        kind = next((v for k, v in kinds.items() if k in cls.split()), "text")
        text = (re.search(r'<div class="text">(.*?)</div>', block, re.S) or [None, ""])[1]
        desc = (re.search(r'<div class="description">(.*?)</div>', block, re.S) or [None, ""])[1]
        opts = [html.unescape(o).strip() for o in re.findall(r'type="(?:radio|checkbox)"[^>]*value="([^"]*)"', block)]
        opts += [html.unescape(o).strip() for o in re.findall(r"<option[^>]*>([^<]+)</option>", block)]
        out.append(dict(label=strip_html(text).replace("✱", "").strip(), required='class="required"' in block, type=kind,
                        options=[o for o in opts if o and not o.lower().startswith("select")],
                        description=strip_html(desc)))
    return out


def fetch_form(ats, slug, job_id, url=""):
    """Return (questions, note). Never raises: a failed fetch just means the drafter works from the posting alone."""
    try:
        if ats == "greenhouse":
            return greenhouse(slug, job_id), "fetched from the Greenhouse boards API"
        if ats == "ashby":
            return ashby(slug, job_id), "fetched from the Ashby job-board API"
        if ats == "lever":
            return lever(url), "parsed from the Lever apply page"
        return [], f"{ats} forms need an account or a multi-step portal; not fetched"
    except Exception as e:  # noqa: BLE001
        return [], f"form fetch failed: {e.__class__.__name__}: {e}"


def approved_document(label):
    """The only approved upload is the B.S. transcript; a field that also mentions graduate transcripts is not it."""
    return "transcript" in label.lower() and APPROVED_DOCS.search(label) and not re.search(r"graduate(?! transcript \(bs\))", label.lower().replace("undergraduate", ""))


def is_russ_question(q):
    """True if only Russ may answer this question (before standing answers are applied)."""
    label = q["label"]
    if label.startswith("EEO: "):
        return False
    if q["type"] == "file":  # any upload beyond the résumé, cover letter and the approved B.S. transcript
        return not (STANDARD_UPLOADS.search(label) or approved_document(label))
    if COUNTRY_QUESTION.search(label):  # which countries / where born: facts only he states ...
        # ... except a plain location 'Country' / 'Country of residence' field whose description is not about citizenship
        plain = re.fullmatch(r"\W*(current )?country( of residence| where you (currently )?(live|reside)| \(?residence\)?)?\W*",
                             label, re.I)
        about_status = re.search(r"citizen|national|export|birth|passport|sanction", q.get("description") or "", re.I)
        if not (plain and not about_status):
            return True
    kind = auth_kind(q)
    if kind is not None:
        return kind == "ask"
    return bool(ASK_RUSS.search(label) or ASK_RUSS_CS.search(label))


def flags(questions, standing=()):
    """Sort a form's questions: ask_russ (only he answers), standing (he already answered that exact question for good),
    disqualify (a required one-way/AI video interview).

    standing: [{"label": the question he answered, "match": optional regex on labels, "answer"}] from
    private/standing_answers.json. A required ask_russ question blocks submission until he answers; an optional one is
    left blank.
    """
    ask, stop, known = [], [], []
    for q in questions:
        label = q["label"]
        if VIDEO_REQUEST.search(label) and not VIDEO_CONSENT.search(label):
            if q["required"]:  # an optional video link is simply left blank
                stop.append(label)
            continue
        if label.startswith("EEO: ") or q["type"] in ("education", "hidden"):
            continue
        if q["type"] == "file":  # an upload is never answered with text: unapproved documents go to him
            if is_russ_question(q):
                ask.append({"label": label, "required": q["required"]})
            continue
        sa = standing_match(label, standing)
        if sa and not standing_fits(q, sa):
            sa = None
        if sa:  # any question he already answered, Russ-only or not, gets his answer again
            known.append({"label": label, "answer": sa["answer"]})
        elif is_russ_question(q):
            ask.append({"label": label, "required": q["required"]})
    return {"ask_russ": ask, "standing": known, "disqualify": stop}


if __name__ == "__main__":
    qs, note = fetch_form(*sys.argv[1:4], *(sys.argv[4:5] or [""]))
    print(json.dumps({"note": note, "questions": qs, "flags": flags(qs)}, indent=1, ensure_ascii=False))
