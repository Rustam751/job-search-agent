"""Build every drafted application: guard, render, one-page auto-fit, PDF, report.

Usage: python build_all.py [key ...]        (no keys = every file in private/applications)

Uses a single Word session for all conversions. Per application it writes, under private/out/<key>/:
  <Display_Name>_Resume_<Company>.docx/.pdf, <Display_Name>_Cover_Letter_<Company>.docx/.pdf,
  form_answers.md, resume.json, cover_letter.json, report.json
"""
import json
import sys
from pathlib import Path

import render_cover_letter
import render_resume
import resume_guard
from word_convert import WordSession

ROOT = Path(__file__).resolve().parent
APPS = ROOT / "private" / "applications"
MASTER = json.loads((ROOT / "private" / "resume_master.json").read_text(encoding="utf-8"))
FITS = [(10.5, 0.6), (10.2, 0.55), (10.0, 0.5), (9.7, 0.45)]


def slug(s):
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


def write_form_answers(app, out):
    """The human-readable answer sheet next to the documents (also refreshed by `daily.py answer`)."""
    lines = [f"# Form answers — {app['company']} — {app['role']}", "", f"Posting: {app['url']}", ""]
    for qa in app.get("custom_answers", []):
        lines += [f"## {qa['question']}", "", qa["answer"], ""]
    if app.get("field_answers"):
        lines += ["## Other form fields", ""]
        for qa in app["field_answers"]:
            flag = " **(needs Russ)**" if qa.get("needs_user") and not str(qa.get("answer", "")).strip() else ""
            src = " (Russ)" if qa.get("source") == "russ" else ""
            lines.append(f"- {qa['question']}: {qa.get('answer', '')}{src}{flag}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "form_answers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_one(app, word):
    from daily import content_hash  # lazy: daily imports this module too
    key = app["key"]
    out = ROOT / "private" / "out" / key
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").unlink(missing_ok=True)  # a failed build must not leave the previous version's report behind
    resume = {**app["resume"], "contact": MASTER["contact"]}
    letter = {**app["cover_letter"], "contact": MASTER["contact"]}
    (out / "resume.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "cover_letter.json").write_text(json.dumps(letter, ensure_ascii=False, indent=1), encoding="utf-8")

    problems = resume_guard.check(MASTER, resume)
    name = MASTER["contact"]["display_name"].replace(" ", "_")
    base_r = out / f"{name}_Resume_{slug(app['company'])}"
    base_c = out / f"{name}_Cover_Letter_{slug(app['company'])}"

    fit = None
    for pt, margin in FITS:
        render_resume.build(resume, base_r.with_suffix(".docx"), body_pt=pt, margin_in=margin)
        word.convert(base_r.with_suffix(".docx"), base_r.with_suffix(".pdf"))
        info_r = render_resume.inspect_pdf(base_r.with_suffix(".pdf"))
        fit = f"{pt}pt / {margin}in"
        if info_r["pages"] == 1:
            break
    info_r = render_resume.inspect_pdf(base_r.with_suffix(".pdf"), png=True)
    info_r["fit"] = fit

    render_cover_letter.build(letter, base_c.with_suffix(".docx"))
    word.convert(base_c.with_suffix(".docx"), base_c.with_suffix(".pdf"))
    info_c = render_resume.inspect_pdf(base_c.with_suffix(".pdf"))

    write_form_answers(app, out)

    report = {"key": key, "company": app["company"], "role": app["role"], "url": app["url"],
              "app_hash": content_hash(key, app), "guard": "OK" if not problems else problems,
              "resume": {"pdf": str(base_r.with_suffix(".pdf")), **info_r},
              "cover_letter": {"pdf": str(base_c.with_suffix(".pdf")), **info_c},
              "notes": app.get("notes", "")}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main():
    keys = sys.argv[1:]
    files = [APPS / f"{k}.json" for k in keys] if keys else sorted(p for p in APPS.glob("*.json") if p.stem.count(".") == 0)
    reports = []
    with WordSession() as word:
        for f in files:
            try:  # one broken application must not stop the others
                app = json.loads(f.read_text(encoding="utf-8"))
                if app.get("key") != f.stem:
                    raise ValueError(f"key inside the file is {app.get('key')!r}")
                r = build_one(app, word)
            except Exception as e:  # noqa: BLE001
                print(f"{f.stem:<20} FAILED: {e.__class__.__name__}: {e}", flush=True)
                continue
            reports.append(r)
            g = r["guard"]
            print(f"{r['key']:<20} resume {r['resume']['pages']}p {r['resume']['words']}w ({r['resume']['fit']})"
                  f" | letter {r['cover_letter']['pages']}p {r['cover_letter']['words']}w"
                  f" | guard {'OK' if g == 'OK' else str(len(g)) + ' PROBLEMS'}", flush=True)
            if g != "OK":
                for x in g[:8]:
                    print("    -", x)
    (ROOT / "private" / "out" / "summary.json").write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
