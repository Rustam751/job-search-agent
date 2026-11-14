"""Deterministic half of the daily job-search run. The LLM stages (scoring, tailoring, submitting) run as
Claude Code workflows in .claude/workflows/ and call these subcommands between stages.

  sweep     [--date D] [--refetch]     fetch + prepare under one date (the daily workflow uses this)
  fetch     [--date D]                 sweep every board in data/companies.json -> data/runs/D/
  prepare   [--date D] [--force]       drop seen / clearly ineligible postings, write scoring batches
  check-scored FILE                    validate a scorer's output (data/runs/D/scored/batch_NN.json)
  check-verdicts FILE                  validate a skeptic's output (batch_NN.verdicts.json)
  merge     [--date D]                 fold valid scores + verdicts into data/scored.json and data/seen.json
  pick      [--date D] [--auto N] [--portal M]   choose today's applications: briefs, live forms, tracker rows
  add       URL [--date D]             queue one specific posting (skips the ranking)
  check-app KEY [--final]              validate private/applications/KEY.json (--final: re-checked + current)
  stamp-recheck KEY                    bind the re-check verdict to the application's current content
  finalize  [--date D]                 statuses for drafted rows, closed postings, and private/digests/D.md
  answer    KEY QUESTION ANSWER [--standing [--match RE] [--negative-ok]]
                                       record Russ's answer (--standing: reuse it for the same question elsewhere;
                                       --match: also for every label the regex matches)
  learn     QUESTION ANSWER [--match RE] [--negative-ok] [--criminal-ok]
                                       a standing answer Russ gave in chat without a specific application; it is applied
                                       to every drafted application that asks it (run ONLY on his message)
  learn-question QUESTION ANSWER --key KEY [--type T] [--option O ...]
                                       gate + memory for a live-form question the plan did not cover (exit 1: do not type it)
  questions                            the open questions for Russ across all applications (deduplicated)
  note      TEXT                       append a mechanics note to private/submit_notes.md (filtered)
  approve   KEY [KEY ...]              record Russ's approval (run ONLY on his explicit message)
  can-submit KEY                       the gate job-submit checks before touching the browser
  start-submit KEY                     gate + mark 'submitting' (run right before filling the form)
  mark      KEY STATUS [--note TEXT] [--force] [--question LABEL ...]
                                       update the tracker (blocked_needs_user --question: blocked on that live-form question)
  status                               tracker summary
  stats                                lifetime pipeline numbers

D defaults to today's local date. Every command prints JSON on stdout for the calling agent.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import fetch_jobs
import forms
import make_batches
import resume_guard
import style_guard
from forms import norm, same_question

ROOT = Path(__file__).resolve().parent
DATA, PRIVATE = ROOT / "data", ROOT / "private"
RUNS, JOBS = DATA / "runs", DATA / "jobs"
SCORED, SEEN = DATA / "scored.json", DATA / "seen.json"
TRACKER, APPS, OUT, DIGESTS = PRIVATE / "tracker.json", PRIVATE / "applications", PRIVATE / "out", PRIVATE / "digests"
STANDING, RUSS = PRIVATE / "standing_answers.json", PRIVATE / "russ_answers.json"
QBANK = PRIVATE / "question_bank.json"  # every question answered on a submitted form: keeps later answers consistent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
if (PRIVATE / "config.local.json").exists():  # personal overrides (e.g. companies to skip) stay out of the repo
    CONFIG.update(json.loads((PRIVATE / "config.local.json").read_text(encoding="utf-8")))
CONFIG.setdefault("skip_companies", {})
SKEPTIC_MIN = min(CONFIG["min_score"], CONFIG["portal_min_score"])  # every pickable score gets a skeptic pass

STATUSES = {"drafting", "ready", "user_applies", "submitting", "submitted", "user_applied", "blocked_needs_user",
            "dropped", "closed", "skipped", "draft_failed", "build_failed"}
ACTIVE = {"drafting", "ready", "user_applies", "submitting", "submitted", "user_applied", "blocked_needs_user"}
ROLE_TYPES = {"applied_ai", "mle", "cv_medical", "swe_ai_adjacent", "research", "other"}
LEVELS = {"new_grad", "entry", "mid_stretch", "senior"}
VARIANTS = {"applied_ai", "mle", "cv_medical"}
SCORED_FIELDS = {"url": str, "company": str, "title": str, "location": str, "posted": str, "compensation": str,
                 "role_type": str, "level": str, "fit_score": int, "eligibility_ok": bool, "eligibility_notes": str,
                 "must_haves_met": str, "strengths": list, "gaps": list, "jd_keywords": list, "resume_variant": str,
                 "cover_letter_angle": str}
# A residency / citizenship / visa claim in text a human reads (the structured authorization fields are checked apart).
WORK_AUTH_CLAIM = re.compile(
    r"green[- ]?card|permanent resident|lawful permanent|\blpr\b|naturali[sz]ed|"
    r"u\.?s\.? (citizen|national)|american citizen|citizen of|citizenship|f-1 (visa|student|status)|"
    r"visa (status|sponsorship|holder|type)|on (an? )?(visa|h-?1b)\b|\bh-?1b\b|asylee|refugee status|resident alien|"
    r"work permit|employment authorization|(?-i:\bOPT\b|\bEAD\b|\bF-1\b|\bCPT\b)"
    + "".join(f"|{t}" for t in forms.PRIVATE_CONFIG.get("extra_status_terms", [])), re.I)
EEO_LIKE = re.compile(r"^eeo: |gender|pronoun|\brace\b|ethnic|hispanic|latin|veteran|disabilit|sexual orientation|"
                      r"transgender|lgbt", re.I)
SALARY_Q = re.compile(r"salary|compensation|pay expectation|desired pay|expected pay", re.I)
SALARY_CONTEXT = re.compile(r"salary|pay range|base pay|base salary|compensation range|annual (base|pay)|hiring range|"
                            r"\bote\b|on-target", re.I)
NOT_SALARY = re.compile(r"stipend|subsidy|credit|reimburse|bonus|allowance|401|equity|funding|raised|valuation|"
                        r"benefit|perk|learning|wellness|relocation", re.I)
STANDARD_FIELDS = {"first name", "last name", "email", "phone", "linkedin profile", "website", "location",
                   "legal first name", "legal last name", "preferred first name", "preferred first last name",
                   "linkedin", "github or personal website", "name", "full name", "current location", "location city",
                   "resume cv", "resume", "cover letter", "phone number", "email address", "linkedin url",
                   "linkedin profile url", "mobile phone", "first name preferred", "legal name"}


# ---------- helpers ----------

def load(path, default=None):
    path = Path(path)
    if not path.exists():
        if default is None:
            sys.exit(f"missing file: {path}")
        return default
    for attempt in range(20):  # Windows: a concurrent os.replace briefly locks the file
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.1)
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # a reader has the file open for a moment
            time.sleep(0.1)
    tmp.replace(path)


def pid_alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def tracker_lock():
    """Exclusive lock around every tracker read-modify-write (the daily run and a submit session can overlap)."""
    return file_lock("tracker.lock")


def answers_lock():
    """Exclusive lock around standing_answers.json, russ_answers.json and the application files `answer`/`learn` edit."""
    return file_lock("answers.lock")


@contextmanager
def file_lock(name):
    lock = PRIVATE / name
    deadline = time.time() + 120
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                holder = int(lock.read_text().strip() or 0)
                stale = not pid_alive(holder) or time.time() - lock.stat().st_mtime > 900
            except (FileNotFoundError, ValueError, PermissionError):
                stale = False  # released or being written right now; just retry
            if stale:
                lock.unlink(missing_ok=True)
            elif time.time() > deadline:
                sys.exit(f"{name} is held by another running process")
            time.sleep(0.3)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def emit(obj):
    print(json.dumps(obj, indent=1, ensure_ascii=False), flush=True)


def run_dir(d):
    return RUNS / d


def standing_answers():
    return [s for s in load(STANDING, []) if isinstance(s, dict) and s.get("answer")]


def russ_record(key):
    """Russ's own answers for one application, recorded only by `daily.py answer` (never by a model)."""
    return load(RUSS, {}).get(key, {})


def row_of(key, tracker=None):
    return next((t for t in (tracker if tracker is not None else load(TRACKER, [])) if t["key"] == key), None)


def batch_outputs(md):
    md = Path(md)
    return {"md": str(md), "scored": str(md.parent.parent / "scored" / f"{md.stem}.json"),
            "verdicts": str(md.parent.parent / "scored" / f"{md.stem}.verdicts.json")}


def batch_urls(batch_md):
    return [line[5:].strip() for line in Path(batch_md).read_text(encoding="utf-8").splitlines() if line.startswith("url: ")]


def scored_entries(obj):
    entries = obj.get("postings", []) if isinstance(obj, dict) else obj
    return entries if isinstance(entries, list) else []


def annual_top(text):
    """Highest annualised figure in salary-context text, or None. '$120-125K' reads as 120,000-125,000."""
    text = text or ""
    amounts = []
    for m in re.finditer(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kKmM])?(?:\s*(?:-|–|—|to)\s*\$?\s?"
                         r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kKmM])?)?", text):
        lo, lo_suf, hi, hi_suf = m.groups()
        suf = (hi_suf or lo_suf or "").lower()
        for num, s in ((lo, lo_suf), (hi, hi_suf)):
            if num:
                amounts.append(float(num.replace(",", "")) * {"k": 1e3, "m": 1e6}.get((s or suf).lower(), 1))
    if not amounts:
        return None
    top = max(amounts)
    if re.search(r"per month|/\s?mo(nth)?\b|monthly", text, re.I):
        top *= 12
    elif re.search(r"per hour|/\s?h(ou)?r\b|hourly|an hour", text, re.I):
        top *= 2080
    return top


def pay_text(rec, scored):
    """The posted pay: the ATS compensation field, else description sentences in a salary context."""
    comp = rec.get("compensation") or scored.get("compensation") or ""
    if "$" in comp:
        return comp
    sents = re.split(r"(?<=[.!?\n])\s+", rec.get("description", ""))
    return " ".join(s for s in sents if "$" in s and SALARY_CONTEXT.search(s) and not NOT_SALARY.search(s))


def below_floor(text):
    top = annual_top(text)
    return top is not None and top >= 1000 and top < CONFIG["salary_floor"]


def make_key(company, title, taken):
    words = re.findall(r"[a-z0-9]+", f"{company} {title}".lower())
    stop = {"the", "and", "of", "a", "i", "ii", "for", "to", "in", "at", "remote", "us", "sf"}
    base = "_".join(w for w in words if w not in stop)[:40].strip("_")
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def job_id_of(rec):
    """ATS job id; records fetched before job_id was stored get it from the URL."""
    if rec.get("job_id"):
        return rec["job_id"]
    m = re.search(r"gh_jid=(\d+)|/jobs/(\d+)|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", rec["url"])
    return next((g for g in m.groups() if g), "") if m else ""


def apply_url(rec):
    """The canonical application page (company career sites often wrap the ATS form in an iframe)."""
    ats, slug, jid = rec["ats"], rec["slug"], rec.get("job_id", "")
    if ats == "greenhouse" and jid:
        return f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}"
    if ats == "ashby" and jid:
        return f"https://jobs.ashbyhq.com/{slug}/{jid}/application"
    if ats == "lever":
        return rec["url"].rstrip("/") + "/apply"
    return rec["url"]


def content_hash(key, app):
    """Hash of what the re-check and the build saw. Entries only Russ answers (his record, Russ-only questions, entries
    still waiting for him) are left out entirely: check-app verifies those deterministically, and recording, filling or
    adding one later must not invalidate an application that was already checked."""
    mine = list(russ_record(key))
    qs = {norm(q["label"]): q for q in load(JOBS / f"{key}.form.json", {"questions": []}).get("questions", [])}

    def his(label):
        label = label if isinstance(label, str) else ""
        q = qs.get(norm(label)) or {"label": label, "type": "text", "options": [], "required": False}
        return any(same_question(label, r) for r in mine) or forms.is_russ_question(q)

    fields = [{k: v for k, v in qa.items() if k not in ("source", "needs_user", "required")}
              for qa in app.get("field_answers", [])
              if isinstance(qa, dict) and not qa.get("needs_user") and not his(qa.get("question"))]
    customs = [qa for qa in app.get("custom_answers", []) if not (isinstance(qa, dict) and his(qa.get("question")))]
    body = {"resume": app.get("resume"), "cover_letter": app.get("cover_letter"), "custom_answers": customs,
            "field_answers": fields}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def clear_artifacts(key):
    """A key that is (re-)tailored must not inherit an old draft's re-check verdict or PDFs."""
    for p in (APPS / f"{key}.json", JOBS / f"{key}.recheck.json", OUT / key / "report.json"):
        p.unlink(missing_ok=True)


# ---------- fetch / prepare ----------

def cmd_fetch(a):
    rd = run_dir(a.date)
    postings, boards, report = fetch_jobs.run(load(DATA / "companies.json"))
    save(rd / "postings.json", postings)
    save(rd / "boards.json", boards)
    (rd / "fetch_report.txt").write_text("\n".join(report), encoding="utf-8")
    bad = [k for k, b in boards.items() if not b["ok"]]
    out = {"date": a.date, "run_dir": str(rd), "boards": len(boards), "boards_ok": len(boards) - len(bad),
           "postings_screened_in": len(postings), "board_errors": bad[:40]}
    if not getattr(a, "quiet", False):
        emit(out)
    return out


def validate_scored(f):
    """(entries, errors) for a scorer file checked against its batch."""
    f = Path(f)
    try:
        raw = load(f)
    except (json.JSONDecodeError, SystemExit, OSError) as e:
        return [], [f"cannot read {f}: {e}"]
    entries = scored_entries(raw)
    md = f.parent.parent / "batches" / f"{f.stem}.md"
    expected = set(batch_urls(md)) if md.exists() else set()
    errs, got, good = [], set(), []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not isinstance(e.get("url"), str):
            errs.append(f"entry {i} is not an object with a url")
            continue
        where = f"entry {i} ({e['url'][:60]})"
        if e.get("compensation") is None:
            e["compensation"] = ""
        bad = [k for k, t in SCORED_FIELDS.items() if not isinstance(e.get(k), t) or (t is int and isinstance(e.get(k), bool))]
        errs += [f"{where}: field {k!r} missing or not {SCORED_FIELDS[k].__name__}" for k in bad]
        if e.get("role_type") not in ROLE_TYPES:
            errs.append(f"{where}: role_type must be one of {sorted(ROLE_TYPES)}")
        if e.get("level") not in LEVELS:
            errs.append(f"{where}: level must be one of {sorted(LEVELS)}")
        if e.get("resume_variant") not in VARIANTS:
            errs.append(f"{where}: resume_variant must be one of {sorted(VARIANTS)}")
        if isinstance(e.get("fit_score"), int) and not 0 <= e["fit_score"] <= 100:
            errs.append(f"{where}: fit_score out of range")
        if e["url"] not in expected:
            errs.append(f"{where}: url is not in the batch")
        got.add(e["url"])
        good.append(e)
    missing = expected - got
    if missing:
        errs.append(f"{len(missing)} batch postings not scored: {sorted(missing)[:5]}")
    return good, errs


def needs_verdict(e):
    return isinstance(e.get("fit_score"), int) and e["fit_score"] >= SKEPTIC_MIN and e.get("eligibility_ok") is True


def valid_verdict(v, urls):
    return (isinstance(v, dict) and v.get("url") in urls and v.get("verdict") in ("keep", "downgrade", "reject")
            and isinstance(v.get("adjusted_score"), int) and not isinstance(v.get("adjusted_score"), bool)
            and 0 <= v["adjusted_score"] <= 100 and isinstance(v.get("reasons"), str) and v["reasons"].strip() != "")


def validate_verdicts(f, entries):
    """(usable verdicts, errors). Invalid verdicts are reported and left out, never raised."""
    try:
        raw = load(f)
    except (json.JSONDecodeError, SystemExit, OSError) as e:
        return [], [f"cannot read {f}: {e}"]
    verdicts = raw.get("verdicts", []) if isinstance(raw, dict) else raw
    if not isinstance(verdicts, list):
        return [], ["'verdicts' must be a list"]
    urls = {e.get("url") for e in entries}
    good = [v for v in verdicts if valid_verdict(v, urls)]
    errs = [f"verdict {i} is invalid (needs url from the scored file, verdict keep|downgrade|reject, "
            f"adjusted_score int 0-100, reasons)" for i, v in enumerate(verdicts) if not valid_verdict(v, urls)]
    have = {v["url"] for v in good}
    missing = [e["url"] for e in entries if needs_verdict(e) and e["url"] not in have]
    if missing:
        errs.append(f"{len(missing)} postings scored >= {SKEPTIC_MIN} have no verdict: {missing[:5]}")
    return good, errs


def cmd_prepare(a):
    rd = run_dir(a.date)
    bdir, sdir = rd / "batches", rd / "scored"
    write_candidate(rd)  # what scorers and skeptics read about the candidate, refreshed every run
    if (bdir / "index.json").exists() and not a.force:  # resume: hand back batches without a valid score + verdict pair
        pending = []
        for p in sorted(bdir.glob("batch_*.md")):
            entries, errs = validate_scored(sdir / f"{p.stem}.json")
            vfile = sdir / f"{p.stem}.verdicts.json"
            if errs or (any(needs_verdict(e) for e in entries) and validate_verdicts(vfile, entries)[1]):
                pending.append(batch_outputs(p))
        out = {"date": a.date, "run_dir": str(rd), "candidate": str(rd / "candidate.md"), "resumed": True,
               "batches": pending, "skeptic_min": SKEPTIC_MIN,
               "kept": len(load(bdir / "index.json")), "deferred_to_later_days": 0,
               "dropped": len(load(bdir / "dropped.json", []))}
        emit(out)
        return out
    for old in list(bdir.glob("batch_*.md")) + (list(sdir.glob("*.json")) if a.force else []):
        old.unlink()
    postings = load(rd / "postings.json")
    seen = load(SEEN, {})
    tracked = {t["url"] for t in load(TRACKER, [])}
    cutoff = (date.fromisoformat(a.date) - timedelta(days=CONFIG["max_posting_age_days"])).isoformat()
    keep, dropped = [], []
    for p in postings:
        if p["url"] in seen or p["url"] in tracked:
            continue
        reason = make_batches.drop_reason(p)
        if not reason and p["posted"] and p["posted"] < cutoff:
            reason = f"posted {p['posted']}, over {CONFIG['max_posting_age_days']} days ago"
        if reason:
            dropped.append({"company": p["company"], "title": p["title"], "url": p["url"], "reason": reason})
            seen[p["url"]] = {"status": "dropped", "reason": reason, "date": a.date}
        else:
            p["description_trimmed"] = make_batches.trim(p["description"])
            keep.append(p)
    # Usage cap: newest postings first; anything past the cap stays unseen and is batched on a later day.
    keep.sort(key=lambda p: p.get("posted", ""), reverse=True)
    cap = CONFIG.get("max_batches_per_run", 4) * CONFIG["batch_size"]
    deferred = len(keep[cap:])
    keep = keep[:cap]
    bdir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)
    paths = make_batches.write_batches(keep, bdir, CONFIG["batch_size"])
    save(bdir / "index.json", keep)
    save(bdir / "dropped.json", dropped)
    save(SEEN, seen)
    out = {"date": a.date, "run_dir": str(rd), "candidate": str(rd / "candidate.md"), "resumed": False,
           "batches": [batch_outputs(p) for p in paths],
           "skeptic_min": SKEPTIC_MIN, "kept": len(keep), "deferred_to_later_days": deferred, "dropped": len(dropped)}
    emit(out)
    return out


def cmd_sweep(a):
    """fetch + prepare under one date, so a run that crosses midnight stays in one folder."""
    if not (run_dir(a.date) / "postings.json").exists() or a.refetch:
        f = cmd_fetch(argparse.Namespace(date=a.date, quiet=True))
    else:
        boards = load(run_dir(a.date) / "boards.json", {})
        f = {"boards": len(boards), "boards_ok": sum(b["ok"] for b in boards.values())}
    print(json.dumps({"fetch": {k: f[k] for k in ("boards", "boards_ok")}}), flush=True)
    cmd_prepare(argparse.Namespace(date=a.date, force=False))


def cmd_check_scored(a):
    entries, errs = validate_scored(a.file)
    out = {"ok": not errs, "entries": len(entries), "errors": errs[:40]}
    if not errs:  # the skeptic reads only what it must re-check
        out["to_verify"] = [e["url"] for e in entries if needs_verdict(e)]
        out["skeptic_packet"] = str(write_skeptic_packet(a.file, entries))
    emit(out)
    sys.exit(1 if errs else 0)


def cmd_check_verdicts(a):
    entries, _ = validate_scored(str(a.file).replace(".verdicts.json", ".json"))
    verdicts, errs = validate_verdicts(a.file, entries)
    emit({"ok": not errs, "verdicts": len(verdicts), "errors": errs[:40]})
    sys.exit(1 if errs else 0)


# ---------- merge / pick ----------

def cmd_merge(a):
    rd = run_dir(a.date)
    index = {p["url"]: p for p in load(rd / "batches" / "index.json", [])}
    scored_all = {s["url"]: s for s in load(SCORED, [])}
    seen = load(SEEN, {})
    merged, unverified, skipped_files, new_good = 0, 0, [], []
    for f in sorted((rd / "scored").glob("batch_*.json")):
        if f.name.endswith(".verdicts.json"):
            continue
        try:
            entries, errs = validate_scored(f)
            if errs:  # left unseen, so the next prepare batches these postings again
                skipped_files.append(f"{f.name}: {errs[0]}")
                continue
            vf = f.with_name(f"{f.stem}.verdicts.json")
            verdicts = {}
            if vf.exists():
                vs, verrs = validate_verdicts(vf, entries)
                if verrs:
                    skipped_files.append(f"{vf.name}: {verrs[0]} (valid verdicts still used)")
                verdicts = {v["url"]: v for v in vs}
            for e in entries:
                rec = index.get(e["url"])
                if not rec:
                    continue
                v = verdicts.get(e["url"])
                if v:
                    final, reasons = v["adjusted_score"], v["reasons"]
                elif needs_verdict(e):  # the skeptic never looked at it: discount like it usually does
                    final, reasons = e["fit_score"] - 10, "not verified by the skeptic; score discounted by 10"
                    unverified += 1
                else:
                    final, reasons = e["fit_score"], ""
                e.update(compensation=e.get("compensation") or "",
                         verdict=v["verdict"] if v else None, final_score=final, verify_reasons=reasons,
                         scored_on=a.date, ats=rec["ats"], slug=rec["slug"], job_id=rec.get("job_id", ""),
                         category=rec.get("category", ""))
                scored_all[e["url"]] = e
                seen[e["url"]] = {"status": "scored", "date": a.date}
                merged += 1
                if e["eligibility_ok"] and final >= CONFIG["min_score"] and e.get("verdict") != "reject":
                    new_good.append(f"{final} {e['company']} — {e['title']}")
        except Exception as ex:  # noqa: BLE001 - one bad file must not lose the day's scoring
            skipped_files.append(f"{f.name}: {ex.__class__.__name__}: {ex}")
    save(SCORED, sorted(scored_all.values(), key=lambda s: -s.get("final_score", 0)))
    save(SEEN, seen)
    emit({"merged": merged, "unverified_discounted": unverified, "skipped_files": skipped_files,
          "scored_total": len(scored_all), "new_at_or_above_min": sorted(new_good, reverse=True)[:30]})


def posting_lookup(d):
    """URL -> screened posting record: today's run first, then older runs and the legacy fetch files."""
    out = {}
    legacy = [DATA / "postings_partial.json", DATA / "postings_new.json"]
    runs = sorted(RUNS.glob("*/postings.json")) if RUNS.exists() else []
    for f in legacy + [r for r in runs if r.parent.name != d] + [run_dir(d) / "postings.json"]:
        for p in load(f, []):
            out[p["url"]] = p
    return out


def private_text(name):
    p = PRIVATE / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else f"({name} is missing)"


def master_compact():
    """The master résumé without the contact block (injected at build time), as one compact JSON line."""
    m = {k: v for k, v in load(PRIVATE / "resume_master.json", {}).items() if k not in ("contact", "_about")}
    return json.dumps(m, ensure_ascii=False)


def context_bundle(form):
    """Everything a drafter or fact-checker needs besides the posting, so each agent reads one file in one turn
    (every extra turn re-sends the agent's whole context)."""
    labels = [q["label"] for q in form]
    bank = [b for b in load(QBANK, []) if isinstance(b, dict) and any(same_question(b.get("question", ""), lab) for lab in labels)]
    L = ["", "---", "", "# Candidate context (copied from the private files when this brief was written)", "",
         "## Master résumé: the ONLY source of facts about him (bullet ids are the source_ids)", "",
         "```json", master_compact(), "```", "",
         "## Rewording rules and name rules (private/tailoring_rules.md)", "", private_text("tailoring_rules.md"), "",
         "## Standard answers (private/application_answers.md)", "", private_text("application_answers.md"), "",
         "## Verified facts for other form questions (private/knowledge.md)", "", private_text("knowledge.md"), "",
         "## His writing rules (private/writing_rules.md)", "", private_text("writing_rules.md"), "",
         "## Sample answers to adapt, never copy (private/answer_bank.md)", "", private_text("answer_bank.md"), ""]
    if bank:
        L += ["## Answers he gave to these same questions on earlier forms (keep facts consistent)", ""]
        L += [f"- {b['question']}: {str(b.get('answer', ''))[:400]} ({b.get('company', '')})" for b in bank[-15:]] + [""]
    return L


def write_candidate(rd):
    """One file with what the scorer and the skeptic need about the candidate (they read it once per agent)."""
    L = ["# Candidate for this run", "", "## Target profile (private/profile.md)", "", private_text("profile.md"), "",
         "## Master résumé: the ONLY facts about the candidate", "", "```json", master_compact(), "```", "",
         "## Standard answers: work authorization, location, start date (private/application_answers.md)", "",
         private_text("application_answers.md"), ""]
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "candidate.md").write_text("\n".join(L), encoding="utf-8")


def write_skeptic_packet(scored_file, entries):
    """Only the postings the skeptic must re-check, each with the first-pass score and its trimmed text."""
    f = Path(scored_file)
    index = {p["url"]: p for p in load(f.parent.parent / "batches" / "index.json", [])}
    todo = [e for e in entries if needs_verdict(e)]
    L = [f"# Postings to re-check ({len(todo)})", ""]
    for e in todo:
        p = index.get(e["url"], {})
        L += [f"## {e.get('company', '')} — {e.get('title', '')}", f"- url: {e['url']}",
              f"- location: {p.get('location', e.get('location', ''))} | posted: {p.get('posted', '')} | pay: {e.get('compensation') or 'not listed'}",
              f"- first pass: fit {e.get('fit_score')}, level {e.get('level')}, role {e.get('role_type')}, "
              f"must-haves {e.get('must_haves_met')}, eligibility: {e.get('eligibility_notes', '')}",
              "- strengths claimed: " + "; ".join(map(str, e.get("strengths", []))),
              "- gaps noted: " + "; ".join(map(str, e.get("gaps", []))), "",
              p.get("description_trimmed") or p.get("description", ""), ""]
    out = f.with_name(f"{f.stem}.skeptic.md")
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def render_brief(rec, s, form, note, fl, bucket, key):
    L = [f"# {rec['company']} — {rec['title']}", "",
         f"- url: {rec['url']}", f"- apply page: {apply_url(rec)}", f"- location: {rec['location']}",
         f"- posted: {rec['posted']}", f"- compensation: {rec.get('compensation') or s.get('compensation') or 'not listed'}",
         f"- ATS: {rec['ats']} (slug {rec['slug']}, job id {rec.get('job_id', '')})",
         f"- résumé variant to use: {s.get('resume_variant', 'applied_ai')}",
         f"- level read: {s.get('level')} | role type: {s.get('role_type')} | score {s.get('fit_score')}/{s.get('final_score')}",
         f"- submission: {'Claude submits after Russ approves' if bucket == 'auto' else 'Russ applies himself (portal needs an account); prepare documents only'}",
         "", "## Screener notes", "", "**Strengths to lead with:**"]
    L += [f"- {x}" for x in s.get("strengths", [])]
    L += ["", "**Gaps / screen-out risks (do NOT paper over these with invented facts):**"]
    L += [f"- {x}" for x in s.get("gaps", [])]
    L += ["", f"**Keywords the posting emphasises:** {', '.join(s.get('jd_keywords', []))}", "",
          f"**Cover-letter angle suggested by the screener:** {s.get('cover_letter_angle', '')}", "",
          f"**Eligibility notes:** {s.get('eligibility_notes', '')}", ""]
    if s.get("verify_reasons"):
        L += [f"**Skeptic's notes:** {s['verify_reasons']}", ""]
    L += [f"## Application form ({note}; full option lists in data/jobs/{key}.form.json)", ""]
    if form:
        for i, q in enumerate(form, 1):
            opts = q["options"]
            shown = " | ".join(opts[:60]) + (f" | (+{len(opts) - 60} more in the form.json)" if len(opts) > 60 else "")
            opts_txt = f" — options: {shown}" if opts else ""
            desc = f" — {q['description'][:400]}" if q["description"] else ""
            L.append(f"{i}. [{'required' if q['required'] else 'optional'}] ({q['type']}) {q['label']}{opts_txt}{desc}")
    else:
        L.append("Form not available. Write 4-6 questions this employer is likely to ask.")
    L += ["", "## Standing answers Russ gave (field_answers, exactly these answers)", ""]
    L += [f"- {x['label']}: {x['answer']}" for x in fl.get("standing", [])] or ["- none apply"]
    L += ["", "## Questions that go to Russ (ONLY in field_answers, needs_user: true, answer \"\"; never in custom_answers)", ""]
    L += [f"- {'[required] ' if q['required'] else ''}{q['label']}" for q in fl["ask_russ"]] or ["- none"]
    L += ["", "## Full posting text", "", rec.get("description", "")]
    L += context_bundle(form)
    return "\n".join(L)


def prepare_job(rec, s, bucket, d, taken_keys, attempts, old=None, source="pick"):
    """Fetch the live form and write the brief. Returns (tracker_row, job) or (None, skip_reason). No tracker writes."""
    rec = {**rec, "job_id": job_id_of(rec)}
    form, note = forms.fetch_form(rec["ats"], rec["slug"], rec["job_id"], rec["url"])
    fl = forms.flags(form, standing_answers())
    if fl["disqualify"]:
        return None, f"form asks for a one-way/AI video interview: {fl['disqualify']}"
    key = (old or {}).get("key") or make_key(rec["company"], rec["title"], taken_keys)  # a retry keeps its key
    taken_keys.add(key)
    clear_artifacts(key)
    JOBS.mkdir(parents=True, exist_ok=True)
    (JOBS / f"{key}.md").write_text(render_brief(rec, s, form, note, fl, bucket, key), encoding="utf-8")
    save(JOBS / f"{key}.form.json", {"note": note, "questions": form, "flags": fl})
    history = f"attempt {attempts - 1}: {old.get('notes', '')}".strip() if old else ""
    row = {"key": key, "company": rec["company"], "role": rec["title"], "url": rec["url"], "apply_url": apply_url(rec),
           "ats": rec["ats"], "slug": rec["slug"], "job_id": rec["job_id"], "status": "drafting", "bucket": bucket,
           "source": source, "picked_at": d, "score": s.get("final_score"), "attempts": attempts,
           "ask_russ": fl["ask_russ"], "notes": history}
    job = {"key": key, "brief": str(JOBS / f"{key}.md"), "company": rec["company"], "role": rec["title"], "bucket": bucket}
    return row, job


def app_is_final(key):
    try:
        return not check_app(key, final=True)
    except Exception:  # noqa: BLE001
        return False


def report_current(key):
    rep = load(OUT / key / "report.json", {})
    app = load(APPS / f"{key}.json", {})
    return bool(rep) and rep.get("guard") == "OK" and rep.get("resume", {}).get("pages") == 1 \
        and rep.get("app_hash") == content_hash(key, app)


def retry_state(tracker, d):
    """Rows from earlier days that need work: build_only (the application passes the final check, only the documents are
    missing or stale — no attempt used) and retry (draft failed or its run died; max 2 attempts)."""
    retry, build_only = {}, []
    for t in tracker:
        if t.get("picked_at", d) >= d or t["status"] not in ("drafting", "draft_failed", "build_failed"):
            continue
        if (APPS / f"{t['key']}.json").exists() and app_is_final(t["key"]):
            if not report_current(t["key"]) or t["status"] != "drafting":
                build_only.append(t["key"])
        elif t.get("attempts", 1) < 2:
            retry[t["url"]] = t
    return retry, build_only


def apply_rows(new_rows, build_only=()):
    """Under the lock: reload the tracker, replace rows for re-queued URLs, reopen build-only rows, append new rows."""
    with tracker_lock():
        tracker = load(TRACKER, [])
        urls = {r["url"] for r in new_rows}
        tracker = [t for t in tracker if t["url"] not in urls or t["status"] not in
                   ("drafting", "draft_failed", "build_failed", "skipped")]
        for t in tracker:
            if t["key"] in build_only:
                t["status"] = "drafting"
        tracker += new_rows
        save(TRACKER, tracker)


def cmd_pick(a):
    rd = run_dir(a.date)
    tracker = load(TRACKER, [])
    today = [t for t in tracker if t.get("picked_at") == a.date and t.get("source") == "pick" and t["status"] != "skipped"]
    if today:  # idempotent: a same-day re-run continues with the jobs already picked (queued ones first come next)
        tailor = [t for t in today if t["status"] == "drafting" and not app_is_final(t["key"])]
        build = [t["key"] for t in today if t["status"] in ("drafting", "build_failed") and app_is_final(t["key"])
                 and not report_current(t["key"])]
        apply_rows([], build)
        jobs = [{"key": t["key"], "brief": str(JOBS / f"{t['key']}.md"), "company": t["company"], "role": t["role"],
                 "bucket": t["bucket"]} for t in tailor]
        jobs, queued = cap_tailoring(jobs)
        emit({"jobs": jobs, "queued_for_next_run": queued, "build_only": build, "resumed": True, "skipped": []})
        return
    postings = {p["url"]: p for p in load(rd / "postings.json")}
    retry, build_only = retry_state(tracker, a.date)
    taken_urls = {t["url"] for t in tracker if t["url"] not in retry}
    taken_keys = {t["key"] for t in tracker}
    per_company = Counter(t["company"].lower() for t in tracker if t["status"] in ACTIVE and t["url"] not in retry)
    skip = {k.lower(): v for k, v in CONFIG["skip_companies"].items()}
    want = {"auto": a.auto if a.auto is not None else CONFIG["auto_per_day"],
            "portal": a.portal if a.portal is not None else CONFIG["portal_per_day"]}
    got, picked_companies, skipped, rows = {"auto": [], "portal": []}, set(), [], []
    cands = sorted(load(SCORED, []), key=lambda s: (s.get("final_score", 0), s.get("posted", "")), reverse=True)
    for s in cands:
        if all(len(got[b]) >= want[b] for b in got):
            break
        url = s["url"]
        if url in taken_urls or not s.get("eligibility_ok") or s.get("verdict") == "reject" or s.get("level") == "senior":
            continue
        rec = postings.get(url)
        if rec is None:  # not in today's sweep: closed, or its board failed today
            continue
        comp = rec["company"].lower()
        bucket = "auto" if rec["ats"] in CONFIG["auto_ats"] else "portal"
        floor = CONFIG["min_score"] if bucket == "auto" else CONFIG["portal_min_score"]
        if s.get("final_score", 0) < floor or len(got[bucket]) >= want[bucket]:
            continue
        why = (skip.get(comp) or ("already at the per-company cap" if per_company[comp] >= CONFIG["max_per_company"] else None)
               or ("another role at this company picked today" if comp in picked_companies else None)
               or ("posted pay is below the salary floor" if below_floor(pay_text(rec, s)) else None))
        if why:
            skipped.append(f"{rec['company']} — {s['title']}: {why}")
            continue
        old = retry.get(url)
        row, job = prepare_job(rec, s, bucket, a.date, taken_keys, (old or {}).get("attempts", 0) + 1, old)
        if row is None:  # remember the disqualification so it is not re-checked every day
            skipped.append(f"{rec['company']} — {s['title']}: {job}")
            key = make_key(rec["company"], rec["title"], taken_keys)
            taken_keys.add(key)
            rows.append({"key": key, "company": rec["company"], "role": rec["title"], "url": url, "ats": rec["ats"],
                         "status": "skipped", "source": "pick", "picked_at": a.date, "notes": job})
            continue
        rows.append(row)
        got[bucket].append(job)
        picked_companies.add(comp)
        per_company[comp] += 1
    apply_rows(rows, build_only)
    jobs, queued = cap_tailoring(got["auto"] + got["portal"])
    save(rd / "picked.json", {"jobs": jobs, "queued_for_next_run": queued, "build_only": build_only})
    emit({"jobs": jobs, "queued_for_next_run": queued, "build_only": build_only, "resumed": False,
          "auto": len(got["auto"]), "portal": len(got["portal"]), "skipped": skipped[:25]})


def cap_tailoring(jobs):
    """At most max_tailor_per_run jobs are tailored per run, so one run stays inside a usage window. The rest are
    marked queued: finalize leaves them alone and the next run (later the same day, or a retry the next day) takes them."""
    cap = CONFIG.get("max_tailor_per_run", 3)
    now, later = jobs[:cap], jobs[cap:]
    with tracker_lock():
        tracker = load(TRACKER, [])
        keys_now, keys_later = {j["key"] for j in now}, {j["key"] for j in later}
        for t in tracker:
            if t["key"] in keys_now:
                t.pop("queued", None)
            elif t["key"] in keys_later:
                t["queued"] = True
        save(TRACKER, tracker)
    return now, [j["key"] for j in later]


def cmd_add(a):
    rec = posting_lookup(a.date).get(a.url)
    if rec is None:
        sys.exit(f"posting not found in any fetched run: {a.url}")
    s = next((x for x in load(SCORED, []) if x["url"] == a.url), {})
    tracker = load(TRACKER, [])
    retry, _ = retry_state(tracker, a.date)
    failed = next((t for t in tracker if t["url"] == a.url and t["status"] == "draft_failed"), None)
    if any(t["url"] == a.url and t["status"] not in ("skipped", "draft_failed") for t in tracker) and a.url not in retry:
        sys.exit("already in the tracker")
    bucket = "auto" if rec["ats"] in CONFIG["auto_ats"] else "portal"
    old = retry.get(a.url) or failed  # a failed draft can be re-queued at once; it keeps its key and counts the attempt
    row, job = prepare_job(rec, s, bucket, a.date, {t["key"] for t in tracker}, (old or {}).get("attempts", 0) + 1,
                           old, source="add")
    if row is None:
        sys.exit(job)
    apply_rows([row])
    emit({"jobs": [job], "next": "run job-tailor for this job, then build_all.py KEY and daily.py finalize"})


# ---------- application checks ----------

def app_shape_errors(app):
    errs = []
    if not isinstance(app, dict):
        return ["application file is not a JSON object"]
    if not isinstance(app.get("resume"), dict):
        errs.append("resume must be an object")
    if not isinstance(app.get("cover_letter"), dict) or not isinstance(app["cover_letter"].get("paragraphs"), list) \
            or not all(isinstance(p, str) for p in app["cover_letter"]["paragraphs"]):
        errs.append("cover_letter must be an object with a list of paragraph strings")
    for k in ("custom_answers", "field_answers"):
        v = app.get(k)
        if not isinstance(v, list) or not all(isinstance(x, dict) and isinstance(x.get("question"), str)
                                              and isinstance(x.get("answer", ""), str) for x in v):
            errs.append(f"{k} must be a list of {{question, answer}} objects with string answers")
    for sec in (app.get("resume") or {}).get("sections", []) if isinstance(app.get("resume"), dict) else []:
        if not isinstance(sec, dict) or not isinstance(sec.get("entries"), list):
            errs.append("every résumé section needs an 'entries' list")
            break
        for e in sec["entries"]:
            if not isinstance(e, dict) or any(not isinstance(b, dict) or not isinstance(b.get("text"), str)
                                              for b in e.get("bullets", [])):
                errs.append("every résumé bullet needs a 'text' string")
                break
    return errs


def check_app(key, final=False, draft=False):
    """Every problem with private/applications/KEY.json; [] means it may be built (final: and shown to Russ)."""
    f = APPS / f"{key}.json"
    if not f.exists():
        return [f"missing {f}"]
    try:
        app = load(f)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]
    shape = app_shape_errors(app)
    if shape:
        return [f"malformed application file: {x}" for x in shape]
    try:
        return _check_app(key, app, final, draft)
    except Exception as e:  # noqa: BLE001 - a crash here must never stop finalize for the other rows
        return [f"malformed application file: {e.__class__.__name__}: {e}"]


def _check_app(key, app, final, draft=False):
    errs = []
    for k in ("key", "company", "role", "url", "resume", "cover_letter", "custom_answers", "field_answers", "notes") +             (() if draft else ("findings",)):
        if k not in app:
            errs.append(f"missing top-level field {k!r}")
    if app.get("key") != key:
        errs.append(f"key inside the file is {app.get('key')!r}, expected {key!r}")
    row = row_of(key)
    if row is None:
        errs.append(f"no tracker row for {key!r} (the key must be copied exactly)")
    elif app.get("url") != row["url"]:
        errs.append("url does not match the tracker row")
    fnd = app.get("findings")
    if draft:
        pass  # a first draft: the fact-check has not run yet
    elif not isinstance(fnd, dict) or not isinstance(fnd.get("findings"), list) or \
            "fact-check failed" in str(fnd.get("keyword_coverage", "")):
        errs.append("no fact-check findings recorded (the adversarial fact-check must run before an application is final)")
    r, c = app["resume"], app["cover_letter"]
    if r.get("variant") not in VARIANTS:
        errs.append("resume.variant must be applied_ai | mle | cv_medical")
    for k in ("summary", "skills", "sections"):
        if not r.get(k):
            errs.append(f"resume.{k} is empty")
    for k in ("date", "company", "role", "greeting", "paragraphs", "closing"):
        if not c.get(k):
            errs.append(f"cover_letter.{k} is empty")
    words = sum(len(p.split()) for p in c.get("paragraphs", []))
    if not 230 <= words <= 400:
        errs.append(f"cover letter is {words} words (want 280-340)")
    master = load(PRIVATE / "resume_master.json")
    errs += [f"guard: {p}" for p in resume_guard.check(master, {**r, "contact": master["contact"]})]

    customs, fields = app["custom_answers"], app["field_answers"]
    questions = load(JOBS / f"{key}.form.json", {"questions": []}).get("questions", [])
    by_label = {norm(q["label"]): q for q in questions}
    fl = forms.flags(questions, standing_answers())
    russ = {norm(q): a for q, a in russ_record(key).items()}

    def q_of(label):
        return by_label.get(norm(label)) or {"label": label, "type": "text", "options": [], "required": False}

    def russ_answered(qa):
        return norm(qa["question"]) in russ and qa.get("answer", "") == russ[norm(qa["question"])]

    for qa in customs + fields:
        if not qa.get("answer", "").strip() and not qa.get("needs_user"):
            errs.append(f"empty answer: {qa['question'][:70]}")
    # questions only Russ answers: every entry that asks one must be empty (needs_user) or carry his recorded answer
    russ_labels = [q["label"] for q in fl["ask_russ"]]
    standing = standing_answers()
    for qa in customs + fields:
        q = q_of(qa["question"])
        unfetched_his = norm(qa["question"]) not in by_label and forms.is_russ_question(q)
        sa = forms.standing_match(q["label"], standing) if unfetched_his else None
        is_his = any(same_question(qa["question"], lab) for lab in russ_labels) or (unfetched_his and not sa)
        answer = qa.get("answer", "").strip()
        if is_his and answer and not russ_answered(qa):
            errs.append(f"only Russ answers this (field_answers, needs_user true, answer \"\"): {qa['question'][:70]}")
        elif sa and answer and not russ_answered(qa) and not forms.standing_answer_ok(answer, sa["answer"], q.get("options") or ()):
            errs.append(f"{qa['question'][:60]}: only Russ's standing answer {sa['answer']!r} may be entered here")
    for q in fl["ask_russ"]:
        if q["required"] and not any(same_question(x["question"], q["label"]) for x in fields):
            errs.append(f"flagged question missing from field_answers (needs_user): {q['label'][:70]}")
    # standing answers are entered exactly as he gave them (or as the form's own spelling of that option)
    for sa in fl.get("standing", []):
        entry = next((x for x in fields if same_question(x["question"], sa["label"])), None)
        if entry is None or not forms.standing_answer_ok(entry.get("answer", "").strip(), sa["answer"],
                                                         q_of(sa["label"]).get("options") or ()):
            errs.append(f"field_answers must answer {sa['label'][:60]!r} with the standing answer {sa['answer']!r}")
    # structured work-authorization answers follow application_answers.md
    for qa in fields:
        if russ_answered(qa) or qa.get("needs_user"):
            continue
        kind = forms.auth_kind(q_of(qa["question"]))
        if kind in ("yes", "no", "other"):
            problem = forms.auth_answer_problem(kind, qa.get("answer"))
            if problem:
                errs.append(f"{qa['question'][:60]}: {problem}")
        elif kind is None and SALARY_Q.search(qa["question"]) and not re.search(r"current|previous|prior|history|last", qa["question"], re.I):
            top = annual_top(qa.get("answer", ""))
            if top is not None and 1000 <= top < CONFIG["salary_floor"]:
                errs.append(f"{qa['question'][:60]}: salary {qa['answer']!r} is below the ${CONFIG['salary_floor']:,} floor")
    # no status claims in anything a human reads (the company name itself may contain such words, e.g. 'Visa')
    company = re.escape(app.get("company", "")) or "$^"
    prose = [("résumé summary", r.get("summary", ""))] + [("cover letter", p) for p in c.get("paragraphs", [])]
    prose += [(qa["question"], qa.get("answer", "")) for qa in customs]
    prose += [(qa["question"], qa.get("answer", "")) for qa in fields
              if not russ_answered(qa) and forms.auth_kind(q_of(qa["question"])) is None]
    for where, text in prose:
        m = WORK_AUTH_CLAIM.search(re.sub(company, " ", text or "", flags=re.I))
        if m:
            errs.append(f"work-authorization wording {m.group(0)!r} in {where[:40]}; it belongs only in the structured field")
            break
    # Russ's writing rules (private/writing_rules.md): the mechanical ones, in everything written in his name
    name = app.get("company", "")
    style = style_guard.check_text("résumé summary", r.get("summary", ""), name)
    for i, p in enumerate(c.get("paragraphs", []), 1):
        style += style_guard.check_text(f"cover letter para {i}", p, name)
    for qa in customs:
        style += style_guard.check_text(qa["question"][:60], qa.get("answer", ""), name)
    standing_labels = [sa["label"] for sa in fl.get("standing", [])]
    for qa in fields:
        q = q_of(qa["question"])
        if russ_answered(qa) or qa.get("needs_user") or forms.auth_kind(q) is not None or forms.is_russ_question(q) \
                or any(same_question(qa["question"], lab) for lab in standing_labels):
            continue  # his own words, or a structured answer the rules above already fix
        style += style_guard.check_field(qa["question"], qa.get("answer", ""), q.get("type", "text"), name,
                                         q.get("options") or ())
    errs += [f"writing rules: {s}" for s in style]
    # every required question has a planned answer
    answered = [qa["question"] for qa in customs + fields] + [s["label"] for s in fl.get("standing", [])]
    for q in questions:
        if not q["required"] or q["type"] in ("file", "education", "hidden"):
            continue
        if EEO_LIKE.search(q["label"]) or norm(q["label"]) in STANDARD_FIELDS:
            continue
        if not any(same_question(q["label"], x) for x in answered):
            errs.append(f"required form question has no answer: {q['label'][:80]}")
    if final:
        rc = load(JOBS / f"{key}.recheck.json", {})
        if rc.get("clean") is not True:
            errs.append("the final re-check of the written application is missing or not clean")
        elif rc.get("app_hash") != content_hash(key, app):
            errs.append("the application changed after its final re-check (stale verdict)")
    return errs


def unanswered_required(key):
    """Required questions still waiting for Russ: the ones only he answers, plus any required question the drafter
    could not answer from the sources and marked needs_user."""
    app = load(APPS / f"{key}.json", {})
    questions = load(JOBS / f"{key}.form.json", {"questions": []}).get("questions", [])
    fl = forms.flags(questions, standing_answers())
    mine = list(russ_record(key))

    def his(label):
        return any(same_question(label, r) for r in mine)

    out = [q["label"] for q in fl["ask_russ"] if q["required"] and not his(q["label"])]
    required = [q["label"] for q in questions if q.get("required")]
    for qa in app.get("field_answers", []) if isinstance(app, dict) else []:
        if not isinstance(qa, dict) or not qa.get("needs_user") or (qa.get("answer") or "").strip():
            continue
        label = qa.get("question", "")
        needed = qa.get("required") or any(same_question(label, r) for r in required)  # 'required': a live-form block
        if needed and not his(label) and not any(same_question(label, o) for o in out):
            out.append(label)
    return out


def standing_used(key):
    questions = load(JOBS / f"{key}.form.json", {"questions": []}).get("questions", [])
    return forms.flags(questions, standing_answers()).get("standing", [])


def cmd_check_app(a):
    errs = check_app(a.key, a.final, a.draft)
    emit({"key": a.key, "ok": not errs, "errors": errs})
    sys.exit(1 if errs else 0)


def cmd_stamp_recheck(a):
    """Called by the re-check agent right after writing its verdict: ties it to the current content."""
    f = JOBS / f"{a.key}.recheck.json"
    rc = load(f, {})
    if not isinstance(rc, dict) or not isinstance(rc.get("clean"), bool):
        sys.exit(f"{f} must hold {{\"clean\": true|false, \"findings\": [...]}}")
    rc["app_hash"] = content_hash(a.key, load(APPS / f"{a.key}.json"))
    save(f, rc)
    emit({"key": a.key, "clean": rc["clean"], "app_hash": rc["app_hash"]})


def cmd_answer(a):
    """Record Russ's answer to one question (--standing: reuse it wherever that same question appears)."""
    if a.match:
        try:
            re.compile(a.match)
        except re.error as e:
            emit({"ok": False, "error": f"bad --match regex: {e}"})
            sys.exit(1)
    with answers_lock():
        _answer(a)


def _answer(a):
    f = APPS / f"{a.key}.json"
    app = load(f)
    questions = load(JOBS / f"{a.key}.form.json", {"questions": []}).get("questions", [])
    labels = [q["label"] for q in questions] + [x["question"] for x in app["field_answers"] + app["custom_answers"]]
    label = next((lab for lab in labels if same_question(lab, a.question)), None)
    if label is None:
        emit({"ok": False, "error": "no question on this form matches; use the exact label",
              "candidates": [q["label"] for q in forms.flags(questions, standing_answers())["ask_russ"]]})
        sys.exit(1)
    record_answer(a.key, label, a.answer)
    applied = [a.key]
    if a.standing:
        entry = add_standing(label, a.answer, a.match, a.negative_ok, a.criminal_ok, a.status_ok)
        applied += [k for k in apply_standing(entry) if k != a.key]
    reopened = reopen_answered(applied)
    emit({"ok": True, "question": label, "answer": a.answer, "applied_to": applied, "reopened": reopened,
          "still_needed": unanswered_required(a.key), "check_app_errors": check_app(a.key)})


def add_standing(label, answer, match=None, negative_ok=False, criminal_ok=False, status_ok=False):
    """Save one standing answer (replacing an older one for the same question) and return it."""
    if match:
        re.compile(match)  # a broken pattern fails here, not silently at every later form
    entry = {"label": label, "answer": answer, "since": date.today().isoformat()}
    if match:
        entry["match"] = match
    if negative_ok:
        entry["negative_ok"] = True
    if criminal_ok:
        entry["criminal_ok"] = True
    if status_ok:
        entry["status_ok"] = True
    sa = [s for s in load(STANDING, []) if not same_question(s.get("label", ""), label)]
    save(STANDING, sa + [entry])
    return entry


def apply_standing(entry):
    """Record a standing answer on every finished application whose form asks that question (the flagged ones only
    Russ answers, and any question left for him). Rows still being drafted are skipped: the fixer may be rewriting that
    file, and check-app makes the draft carry the standing answer anyway. Returns the keys it changed."""
    changed = []
    for t in load(TRACKER, []):
        if t["status"] not in ("ready", "blocked_needs_user") or not (APPS / f"{t['key']}.json").exists():
            continue
        qs = load(JOBS / f"{t['key']}.form.json", {"questions": []}).get("questions", [])
        by_label = {norm(q["label"]): q for q in qs}
        app = load(APPS / f"{t['key']}.json", {})
        waiting = [x["question"] for x in app.get("field_answers", []) if isinstance(x, dict) and x.get("needs_user")]
        labels = [q["label"] for q in qs if forms.is_russ_question(q)] + waiting
        hits = set()
        for lab in labels:
            q = by_label.get(norm(lab)) or {"label": lab, "type": "text", "options": [], "required": False}
            if forms.standing_match(lab, [entry]) and forms.standing_fits(q, entry):
                hits.add(lab)
        for lab in sorted(hits):
            record_answer(t["key"], lab, entry["answer"])
        if hits:
            changed.append(t["key"])
    return changed


def reopen_answered(keys):
    """A row blocked only on questions for him goes back to ready once they are answered (a fresh approval is still
    required). A row blocked for anything else (a CAPTCHA, an agreement) stays blocked."""
    reopened = []
    with tracker_lock():
        tracker = load(TRACKER, [])
        for t in tracker:
            if t["key"] in keys and t["status"] == "blocked_needs_user" and t.get("blocked_on") \
                    and not unanswered_required(t["key"]):
                t["status"] = "ready"
                t.pop("approved_at", None)
                t.pop("approved_hash", None)
                t.pop("blocked_on", None)
                reopened.append(t["key"])
        save(TRACKER, tracker)
    return reopened


def cmd_learn(a):
    """A standing answer Russ gave in chat, not tied to one application."""
    try:
        with answers_lock():
            entry = add_standing(a.question, a.answer, a.match, a.negative_ok, a.criminal_ok, a.status_ok)
            applied = apply_standing(entry)
    except re.error as e:
        emit({"ok": False, "error": f"bad --match regex: {e}"})
        sys.exit(1)
    emit({"ok": True, "standing": entry, "applied_to": applied, "reopened": reopen_answered(applied),
          "still_needed": {k: unanswered_required(k) for k in applied}})


def remember(rows):
    """Append answered questions to the question bank (one entry per question per application)."""
    bank = load(QBANK, [])
    seen = {(b.get("key"), norm(b.get("question"))) for b in bank}
    for r in rows:
        if (r["key"], norm(r["question"])) not in seen and (r.get("answer") or "").strip():
            bank.append(r)
            seen.add((r["key"], norm(r["question"])))
    save(QBANK, bank)


def learn_from_app(key):
    """After a submission: keep every question it answered, so later drafts stay consistent and new question types
    are recognised. Standard fields and EEO answers are left out (they live in application_answers.md)."""
    app = load(APPS / f"{key}.json", {})
    t = row_of(key) or {}
    mine, standing = list(russ_record(key)), standing_answers()
    rows = []
    for kind in ("custom_answers", "field_answers"):
        for qa in app.get(kind, []) if isinstance(app, dict) else []:
            q = qa.get("question", "") if isinstance(qa, dict) else ""
            if not q or EEO_LIKE.search(q) or norm(q) in STANDARD_FIELDS or forms.auth_kind({"label": q, "type": "text", "options": []}):
                continue
            if any(same_question(q, m) for m in mine) and not forms.standing_match(q, standing):
                continue  # a one-off answer he gave for this company only: never reused
            rows.append({"question": q, "answer": qa.get("answer", ""), "kind": kind.split("_")[0], "key": key,
                         "company": t.get("company", app.get("company", "")), "date": date.today().isoformat()})
    remember(rows)
    return len(rows)


def check_unplanned(question, answer, qtype="text", options=(), company=""):
    """Problems with an answer the submit agent wants to type for a question the plan did not cover. Any problem
    means: do not type it, stop and mark the row blocked on that question."""
    q = {"label": question, "type": qtype, "options": list(options), "required": True, "description": ""}
    problems = []
    if forms.is_russ_question(q):
        sa = forms.standing_match(question, standing_answers())
        if not sa or not forms.standing_fits(q, sa):
            return ["only Russ answers this question and he has no standing answer for it: stop, mark the row blocked on it"]
        if not forms.standing_answer_ok(answer, sa["answer"], list(options)):
            return [f"only Russ's standing answer {sa['answer']!r} may be entered here"]
        return []
    kind = forms.auth_kind(q)
    if kind == "ask":
        return ["a mixed work-authorization question: only Russ answers it; stop"]
    if kind in ("yes", "no", "other"):
        p = forms.auth_answer_problem(kind, answer)
        if p:
            problems.append(p)
    elif WORK_AUTH_CLAIM.search(answer or ""):
        problems.append("work-authorization wording belongs only in a structured authorization field")
    if SALARY_Q.search(question) and re.search(r"current|previous|prior|history|last", question, re.I):
        problems.append("salary history: only Russ answers it; stop")
    problems += style_guard.check_field(question, answer, qtype, company, list(options))
    return problems


def cmd_learn_question(a):
    """The gate for a question the plan did not cover: checks the answer, and only then remembers it for later forms.
    Exit 1 means do not type that answer."""
    t = row_of(a.key)
    problems = check_unplanned(a.question, a.answer, a.type, a.option or (), (t or {}).get("company", ""))
    if problems:
        emit({"ok": False, "question": a.question, "problems": problems})
        sys.exit(1)
    remember([{"question": a.question, "answer": a.answer, "kind": "unplanned", "key": a.key,
               "company": (t or {}).get("company", ""), "date": date.today().isoformat()}])
    emit({"ok": True, "question": a.question})


NOTE_DENY = re.compile(r"opt[- ]?in|agree|consent|arbitration|password|sign[- ]?in|log ?in|account|verification|code|"
                       r"captcha|ignore|instruction|always (tick|check)|must (tick|check|accept)|skip the|bypass|"
                       r"@|https?://(?!job-boards\.greenhouse\.io|jobs\.ashbyhq\.com|jobs\.lever\.co)", re.I)


def cmd_note(a):
    """Append one mechanics note to private/submit_notes.md, which submit agents read next to docs/SUBMITTING.md.
    Refused if it looks like a page's instruction, an agreement, a credential or personal data. The notes stay private
    because they can name the companies he applied to; general lessons are copied into SUBMITTING.md by hand."""
    text = " ".join((a.text or "").split())
    if not 20 <= len(text) <= 400 or NOTE_DENY.search(text) or "—" in text:
        emit({"ok": False, "error": "note refused: 20-400 characters, mechanics only (no agreements, opt-ins, sign-in, "
                                    "codes, page instructions, links outside the ATS domains or personal data)"})
        sys.exit(1)
    doc = PRIVATE / "submit_notes.md"
    body = doc.read_text(encoding="utf-8") if doc.exists() else \
        "# Learned notes from submit agents (private)\n\nHints about form mechanics. They never override the hard stops.\n"
    doc.write_text(body.rstrip() + f"\n- {date.today().isoformat()}: {text}\n", encoding="utf-8")
    emit({"ok": True, "note": text})


def open_questions(tracker=None):
    """Every question waiting for Russ, deduplicated across applications: [{question, keys}]."""
    out = []
    for t in tracker if tracker is not None else load(TRACKER, []):
        if t["status"] not in ("ready", "blocked_needs_user", "drafting") or not (APPS / f"{t['key']}.json").exists():
            continue
        for q in unanswered_required(t["key"]):
            hit = next((o for o in out if same_question(o["question"], q)), None)
            if hit:
                hit["keys"].append(t["key"])
            else:
                out.append({"question": q, "keys": [t["key"]]})
    return out


def cmd_questions(a):
    emit({"open_questions": open_questions()})


def record_answer(key, label, answer):
    rec = load(RUSS, {})
    rec.setdefault(key, {})[label] = answer
    save(RUSS, rec)
    f = APPS / f"{key}.json"
    app = load(f)
    entry = next((x for x in app["field_answers"] if same_question(x["question"], label)), None)
    if entry is None:
        entry = {"question": label}
        app["field_answers"].append(entry)
    entry.update(answer=answer, needs_user=False)
    app["custom_answers"] = [x for x in app["custom_answers"] if not same_question(x["question"], label)]
    save(f, app)
    import build_all  # refresh the human-readable answers file next to the documents
    build_all.write_form_answers(app, OUT / key)


# ---------- finalize / digest ----------

def closed_on_board(t, boards):
    """True when the row's board was fetched fine in this run and no longer lists the posting."""
    b = boards.get(f"{t.get('ats')}:{t.get('slug')}")
    return bool(b and b.get("ok") and t["url"] not in b.get("urls", []))


def finalize_row(t, d, boards):
    """Set one drafting row's status; returns the problem text ('' when fine)."""
    key = t["key"]
    if closed_on_board(t, boards):
        t["status"] = "closed"
        return "the posting was taken down"
    if not (APPS / f"{key}.json").exists():
        t["status"] = "draft_failed"
        return "no application file was written"
    errs = check_app(key, final=True)
    if errs:
        t["status"] = "draft_failed"
        return "; ".join(errs[:4])
    if not report_current(key):
        t["status"] = "build_failed"
        return "documents missing, stale, over one page, or failed the guard"
    r = load(OUT / key / "report.json")
    t["status"] = "ready" if t.get("bucket") == "auto" else "user_applies"
    t["resume_pdf"], t["cover_letter_pdf"] = r["resume"]["pdf"], r["cover_letter"]["pdf"]
    return ""


def cmd_finalize(a):
    rd = run_dir(a.date)
    boards = load(rd / "boards.json", {})
    problems = []
    with tracker_lock():
        tracker = load(TRACKER, [])
        for t in tracker:
            if t["status"] in ("ready", "user_applies") and closed_on_board(t, boards):
                t["status"], t["notes"] = "closed", (t.get("notes", "") + " posting taken down").strip()
                problems.append(f"{t['company']} — {t['role']}: posting taken down; removed from the approval list")
                continue
            if t["status"] != "drafting" or t.get("picked_at", a.date) > a.date or t.get("queued"):
                continue  # queued rows wait for the next run
            try:
                why = finalize_row(t, a.date, boards)
            except Exception as e:  # noqa: BLE001 - one bad row must not block the others or the digest
                t["status"], why = "draft_failed", f"finalize error: {e.__class__.__name__}: {e}"
            if why:
                if t.get("attempts", 1) >= 2 and t["status"] == "draft_failed":
                    why += " (second attempt; not retried)"
                t["notes"] = (t.get("notes", "") + " " + why).strip()
                problems.append(f"{t['company']} — {t['role']}: {why}")
        save(TRACKER, tracker)
    for p in getattr(a, "extra_problems", None) or []:
        problems.insert(0, p)
    digest = write_digest(a.date, rd, tracker, problems)
    emit({"digest": str(digest), "ready": [t["key"] for t in tracker if t["status"] == "ready"],
          "user_applies": [t["key"] for t in tracker if t["status"] == "user_applies"], "problems": problems})


def write_digest(d, rd, tracker, problems):
    boards = load(rd / "boards.json", {})
    bdir = rd / "batches"
    kept, dropped = len(load(bdir / "index.json", [])), len(load(bdir / "dropped.json", []))
    scored = {s["url"]: s for s in load(SCORED, [])}
    scored_today = [s for s in scored.values() if s.get("scored_on") == d]
    good = [s for s in scored_today if s["eligibility_ok"] and s.get("final_score", 0) >= CONFIG["min_score"]
            and s.get("verdict") != "reject"]
    L = [f"# Job search — {d}", "",
         f"Swept {sum(b['ok'] for b in boards.values())}/{len(boards)} boards · {kept} new postings scored in batches, "
         f"{dropped} dropped by rules · {len(scored_today)} scored today, {len(good)} at {CONFIG['min_score']}+.", ""]
    ready = [t for t in tracker if t["status"] == "ready"]
    num = {t["key"]: i for i, t in enumerate(ready, 1)}
    asks = open_questions(tracker)
    if asks:  # one list, each question once: his answer becomes a standing answer for every later form
        L += [f"## Questions for you ({len(asks)})", "",
              "Answer these once (\"Q1: yes\"). Each answer is saved and reused on every later form that asks it.", ""]
        for i, q in enumerate(asks, 1):
            where = ", ".join(f"#{num[k]}" if k in num else f"`{k}`" for k in q["keys"])
            L.append(f"- **Q{i}.** {q['question']} ({where})")
        L.append("")
    L += [f"## Ready for your approval ({len(ready)})", ""]
    if ready:
        L += ["| # | Key | Company | Role | Where | Pay | Score |", "|---|---|---|---|---|---|---|"]
        for i, t in enumerate(ready, 1):
            s = scored.get(t["url"], {})
            L.append(f"| {i} | `{t['key']}` | {t['company']} | {t['role']} | {s.get('location', '')[:40]} | "
                     f"{(s.get('compensation') or 'not listed')[:30]} | {t.get('score', '')} |")
        L.append("")
        for i, t in enumerate(ready, 1):
            s = scored.get(t["url"], {})
            L += [f"**{i}. {t['company']} — {t['role']}** (`{t['key']}`, picked {t.get('picked_at', '')}) — {t['url']}",
                  f"- angle: {s.get('cover_letter_angle', '')}",
                  f"- biggest gap: {(s.get('gaps') or [''])[0]}",
                  f"- documents: {t.get('resume_pdf', '')} · {t.get('cover_letter_pdf', '')} · "
                  f"answers: {OUT / t['key'] / 'form_answers.md'}"]
            for q in unanswered_required(t["key"]):
                L.append(f"- **needs your answer first:** \"{q}\"")
            for sa in standing_used(t["key"]):
                L.append(f"- your standing answer will be entered: \"{sa['label']}\" → {sa['answer']}")
            L.append("")
        L += ["Reply with numbers from this table (\"approve 1 2 4\", \"approve all\", \"skip 3\"), and answer any "
              "\"needs your answer first\" question in the same message.", ""]
    else:
        L += ["Nothing new cleared the bar today.", ""]
    queued = [t for t in tracker if t["status"] == "drafting" and t.get("queued")]
    if queued:
        L += [f"{len(queued)} more picked today are queued for the next run: "
              + ", ".join(f"{t['company']} ({t['role'][:40]})" for t in queued) + ".", ""]
    mine = [t for t in tracker if t["status"] == "user_applies"]
    if mine:
        L += [f"## Apply yourself — these portals need an account ({len(mine)})", ""]
        L += [f"- {t['company']} — {t['role']} — {t['url']} — documents in {OUT / t['key']}" for t in mine] + [""]
    blocked = [t for t in tracker if t["status"] in ("blocked_needs_user", "submitting")]
    if blocked:
        L += ["## Blocked on you", ""]
        L += [f"- {t['company']} — {t['role']}: " + ("submission outcome unknown — check your email for a confirmation "
              "before anything is retried" if t["status"] == "submitting" else t.get("notes", "")[-260:]) for t in blocked] + [""]
    if problems:
        L += ["## Problems in today's run", ""] + [f"- {p}" for p in problems] + [""]
    counts = Counter(t["status"] for t in tracker)
    L += ["## Tracker", "", " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())), ""]
    DIGESTS.mkdir(parents=True, exist_ok=True)
    out = DIGESTS / f"{d}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


# ---------- approval / submission gate / tracker ----------

def cmd_approve(a):
    done, refused = [], []
    with tracker_lock():
        tracker = load(TRACKER, [])
        for key in a.keys:
            t = row_of(key, tracker)
            if t is None or t["status"] != "ready":
                refused.append(f"{key}: status is {t['status'] if t else 'missing'}, not ready")
                continue
            t["approved_at"] = date.today().isoformat()
            t["approved_hash"] = content_hash(key, load(APPS / f"{key}.json", {}))  # the version he approved
            done.append(key)
        save(TRACKER, tracker)
    emit({"approved": done, "refused": refused})


def can_submit(key, tracker=None):
    t = row_of(key, tracker)
    if t is None:
        return False, ["no tracker row"], {}
    errs = []
    if t["status"] != "ready" or t.get("submitted_at"):
        errs.append(f"status is {t['status']}" + (" — never submit twice; check email / the tracker note"
                                                  if t["status"] in ("submitting", "submitted") or t.get("submitted_at") else ""))
    if not t.get("approved_at"):
        errs.append("Russ has not approved it (daily.py approve)")
    elif t.get("approved_hash") != content_hash(key, load(APPS / f"{key}.json", {})):
        errs.append("the application changed after Russ approved it; it needs a fresh approval")
    if not report_current(key):
        errs.append("documents are not built for the current version of the application")
    errs += [f"check-app: {e}" for e in check_app(key, final=True)]
    errs += [f"needs Russ's answer: {q}" for q in unanswered_required(key)]
    rep = load(OUT / key / "report.json", {})
    info = {"apply_url": t.get("apply_url") or t["url"], "resume_pdf": rep.get("resume", {}).get("pdf"),
            "cover_letter_pdf": rep.get("cover_letter", {}).get("pdf"), "ats": t.get("ats"),
            "standing_answers": standing_used(key)}
    return not errs, errs, info


def cmd_can_submit(a):
    ok, errs, info = can_submit(a.key)
    emit({"key": a.key, "ok": ok, "errors": errs, **info})
    sys.exit(0 if ok else 1)


def cmd_start_submit(a):
    """The gate plus a 'submitting' mark, taken together under the lock right before the form is filled."""
    with tracker_lock():
        tracker = load(TRACKER, [])
        ok, errs, info = can_submit(a.key, tracker)
        if ok:
            t = row_of(a.key, tracker)
            t["status"], t["submit_started"] = "submitting", time.strftime("%Y-%m-%d %H:%M")
            save(TRACKER, tracker)
    emit({"key": a.key, "ok": ok, "errors": errs, **info})
    sys.exit(0 if ok else 1)


def cmd_mark(a):
    if a.status not in STATUSES:
        sys.exit(f"status must be one of {sorted(STATUSES)}")
    with tracker_lock():
        tracker = load(TRACKER, [])
        t = row_of(a.key, tracker)
        if t is None:
            sys.exit(f"no tracker row {a.key!r}")
        if (t["status"] == "submitted" or t.get("submitted_at")) and a.status != "submitted" and not a.force:
            sys.exit("this application was submitted; only --force changes that")
        if a.status not in ("ready", "submitting", "submitted"):
            t.pop("approved_at", None)  # any detour needs a fresh approval
            t.pop("approved_hash", None)
        if a.status == "blocked_needs_user" and a.question:
            t["blocked_on"] = a.question  # reopened by answer/learn once these are answered
        t["status"] = a.status
        if a.status in ("submitted", "user_applied"):
            t["submitted_at"] = date.today().isoformat()
        if a.note:
            t["notes"] = (t.get("notes", "") + " " + a.note).strip()
        save(TRACKER, tracker)
    if a.status == "blocked_needs_user" and a.question and (APPS / f"{a.key}.json").exists():
        with answers_lock():  # a live-form question: add it to the plan so answer / questions / the digest see it
            app = load(APPS / f"{a.key}.json")
            for label in a.question:
                entry = next((x for x in app["field_answers"] if same_question(x["question"], label)), None)
                if entry is None:
                    app["field_answers"].append({"question": label, "answer": "", "needs_user": True, "required": True})
                elif not russ_record(a.key).get(label):
                    entry.update(needs_user=True, required=True, answer="")
            save(APPS / f"{a.key}.json", app)
    learned = 0
    if a.status == "submitted":
        try:
            learned = learn_from_app(a.key)
        except Exception as e:  # noqa: BLE001 - the submission itself is recorded; learning is a bonus
            learned = f"not recorded: {e.__class__.__name__}: {e}"
    emit({"key": a.key, "status": a.status, "questions_learned": learned})


def cmd_status(a):
    tracker = load(TRACKER, [])
    emit({"counts": Counter(t["status"] for t in tracker),
          "rows": [f"{t['status']:<18} {t.get('submitted_at') or t.get('picked_at', ''):<10} {t['company']} — {t['role']} ({t['key']})"
                   for t in sorted(tracker, key=lambda t: t["status"])]})


def cmd_stats(a):
    companies = load(DATA / "companies.json")
    seen = load(SEEN, {})
    tracker = load(TRACKER, [])
    findings = Counter()
    for f in APPS.glob("*.json"):
        try:
            for x in (load(f).get("findings") or {}).get("findings", []):
                findings[x.get("severity", "?")] += 1
        except Exception:  # noqa: BLE001
            continue
    runs = sorted(p.name for p in RUNS.glob("*") if p.is_dir()) if RUNS.exists() else []
    emit({"boards_monitored": Counter(c["ats"] for c in companies if c.get("slug")),
          "postings_screened": len(seen), "by_outcome": Counter(v["status"] for v in seen.values()),
          "scored": len(load(SCORED, [])), "daily_runs": len(runs), "first_run": runs[0] if runs else None,
          "applications_built": len(list(OUT.glob("*/report.json"))),
          "fact_check_findings_fixed": findings, "tracker": Counter(t["status"] for t in tracker)})


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    today = date.today().isoformat()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("sweep", "fetch", "prepare", "merge", "pick", "finalize"):
        s = sub.add_parser(name)
        s.add_argument("--date", default=today)
        if name == "prepare":
            s.add_argument("--force", action="store_true")
        if name == "sweep":
            s.add_argument("--refetch", action="store_true")
        if name == "pick":
            s.add_argument("--auto", type=int)
            s.add_argument("--portal", type=int)
        if name == "finalize":
            s.add_argument("--problem", dest="extra_problems", action="append", help="a line to add to the digest")
    s = sub.add_parser("add")
    s.add_argument("url")
    s.add_argument("--date", default=today)
    for name in ("check-scored", "check-verdicts"):
        sub.add_parser(name).add_argument("file")
    s = sub.add_parser("check-app")
    s.add_argument("key")
    s.add_argument("--final", action="store_true")
    s.add_argument("--draft", action="store_true", help="a first draft: everything except the fact-check record")
    sub.add_parser("stamp-recheck").add_argument("key")
    s = sub.add_parser("answer")
    s.add_argument("key")
    s.add_argument("question")
    s.add_argument("answer")
    s.add_argument("--standing", action="store_true", help="also use this answer wherever the same question appears")
    s.add_argument("--match", help="with --standing: a regex; the answer also covers every label it matches")
    s.add_argument("--negative-ok", action="store_true", help="the --match regex may cover a wrongdoing question (a 'No')")
    s.add_argument("--criminal-ok", action="store_true", help="the --match regex holds his own criminal-history answer")
    s.add_argument("--status-ok", action="store_true", help="the --match regex may answer a work-status select that offers exactly this option")
    s = sub.add_parser("learn")
    s.add_argument("question")
    s.add_argument("answer")
    s.add_argument("--match", help="a regex; the answer also covers every label it matches")
    s.add_argument("--negative-ok", action="store_true", help="the --match regex may cover a wrongdoing question (a 'No')")
    s.add_argument("--criminal-ok", action="store_true", help="the --match regex holds his own criminal-history answer")
    s.add_argument("--status-ok", action="store_true", help="the --match regex may answer a work-status select that offers exactly this option")
    s = sub.add_parser("learn-question")
    s.add_argument("question")
    s.add_argument("answer")
    s.add_argument("--key", required=True)
    s.add_argument("--type", default="text", help="text | long_text | select | multi_select | boolean | number")
    s.add_argument("--option", action="append", help="for a select: each option the live form offers (repeat)")
    sub.add_parser("questions")
    sub.add_parser("note").add_argument("text")
    sub.add_parser("approve").add_argument("keys", nargs="+")
    sub.add_parser("can-submit").add_argument("key")
    sub.add_parser("start-submit").add_argument("key")
    s = sub.add_parser("mark")
    s.add_argument("key")
    s.add_argument("status")
    s.add_argument("--note", default="")
    s.add_argument("--force", action="store_true")
    s.add_argument("--question", action="append",
                   help="with blocked_needs_user: the exact label of a live-form question only Russ can answer (repeat)")
    sub.add_parser("status")
    sub.add_parser("stats")
    a = p.parse_args()
    {"sweep": cmd_sweep, "fetch": cmd_fetch, "prepare": cmd_prepare, "check-scored": cmd_check_scored,
     "check-verdicts": cmd_check_verdicts, "merge": cmd_merge, "pick": cmd_pick, "add": cmd_add,
     "check-app": cmd_check_app, "stamp-recheck": cmd_stamp_recheck, "answer": cmd_answer, "finalize": cmd_finalize,
     "learn": cmd_learn, "learn-question": cmd_learn_question, "questions": cmd_questions, "note": cmd_note,
     "approve": cmd_approve, "can-submit": cmd_can_submit, "start-submit": cmd_start_submit, "mark": cmd_mark,
     "status": cmd_status, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    main()
