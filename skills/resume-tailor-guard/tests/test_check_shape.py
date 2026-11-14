"""Unit tests for scripts/check_shape.py.

Run from the skill folder:  python -m unittest discover -s tests
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_shape as shape  # noqa: E402

SCRIPT = str(ROOT / "scripts" / "check_shape.py")
EXAMPLE = json.loads((ROOT / "examples" / "tailored.json").read_text(encoding="utf-8"))


def app():
    return copy.deepcopy(EXAMPLE)


def first_entry(a):
    return a["resume"]["sections"][0]["entries"][0]


class Shape(unittest.TestCase):
    def errors(self, a, **options):
        return shape.check(a, **options)[0]

    def assertFlags(self, a, fragment, **options):
        errs = self.errors(a, **options)
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")

    def test_example_passes_as_draft_and_as_final(self):
        self.assertEqual(shape.check(EXAMPLE), ([], []))
        self.assertEqual(shape.check(EXAMPLE, final=True), ([], []))

    def test_must_be_an_object(self):
        self.assertTrue(self.errors([]))

    def test_company_and_role(self):
        a = app()
        a["company"], a["role"] = "", None
        self.assertFlags(a, "company is empty")
        self.assertFlags(a, "role is empty")

    def test_notes_must_be_text(self):
        a = app()
        a["notes"] = ["check the dates"]
        self.assertFlags(a, "notes must be a string")
        del a["notes"]
        self.assertEqual(self.errors(a), [])

    def test_resume_parts(self):
        a = app()
        a["resume"]["summary"] = "  "
        self.assertFlags(a, "resume.summary is empty")
        a = app()
        a["resume"]["sections"] = []
        self.assertFlags(a, "resume.sections is empty")
        a = app()
        del a["resume"]
        self.assertFlags(a, "resume must be an object")
        a = app()
        a["resume"]["sections"][0]["entries"] = []
        self.assertFlags(a, 'every resume section needs a "title" and a non-empty "entries" list')

    def test_skills(self):
        a = app()
        a["resume"]["skills"] = []
        self.assertEqual(self.errors(a), [])  # a résumé may have no skills section
        for bad in ("Python", [{"label": "Languages"}], [{"label": "", "items": ["Python"]}],
                    [{"label": "Languages", "items": []}], [{"label": "Languages", "items": ["Python", 3]}]):
            a["resume"]["skills"] = bad
            self.assertFlags(a, "resume.skills must be a list")

    def test_entries_and_bullets(self):
        a = app()
        del first_entry(a)["heading"]
        self.assertFlags(a, 'every entry needs a "heading"')
        a = app()
        first_entry(a)["dates"] = 2024
        self.assertFlags(a, "dates must be a string")
        a = app()
        first_entry(a)["bullets"] = "Built pipelines."
        self.assertFlags(a, "bullets must be a list")
        for bad in ("a plain string", {"text": "no ids"}, {"text": "empty ids", "source_ids": []},
                    {"text": "", "source_ids": ["ql1"]}, {"text": "bad id", "source_ids": [1]}):
            a = app()
            first_entry(a)["bullets"].append(bad)
            self.assertFlags(a, 'every bullet needs a "text" and a non-empty "source_ids" list')

    def test_entry_without_bullets_is_fine(self):
        a = app()
        del a["resume"]["sections"][2]["entries"][0]["bullets"]
        self.assertEqual(self.errors(a), [])

    def test_cover_letter_is_required_unless_resume_only(self):
        a = app()
        del a["cover_letter"]
        self.assertFlags(a, "cover_letter must be an object")
        self.assertEqual(self.errors(a, resume_only=True), [])

    def test_a_letter_that_is_present_is_always_checked(self):
        a = app()
        a["cover_letter"]["greeting"] = ""
        self.assertFlags(a, "cover_letter.greeting is empty", resume_only=True)
        a["cover_letter"]["paragraphs"] = "one long string"
        self.assertFlags(a, "cover_letter.paragraphs must be a non-empty list")

    def test_cover_letter_length(self):
        a = app()
        a["cover_letter"]["paragraphs"] = a["cover_letter"]["paragraphs"][:1]
        self.assertFlags(a, "allowed 230-400, aim for 280-340")
        a["cover_letter"]["paragraphs"] = ["word " * 401]
        self.assertFlags(a, "cover letter is 401 words")
        a["cover_letter"]["paragraphs"] = ["word " * 400]
        self.assertEqual(self.errors(a), [])
        a["cover_letter"]["paragraphs"] = ["word " * 230]
        self.assertEqual(self.errors(a), [])
        self.assertFlags(a, "allowed 250-300", letter_range=(250, 300))

    def test_letter_outside_the_target_is_a_note_not_an_error(self):
        a = app()
        a["cover_letter"]["paragraphs"] = ["word " * 250]
        errs, notes = shape.check(a)
        self.assertEqual(errs, [])
        self.assertTrue(any("250 words" in n for n in notes), notes)

    def test_resume_length(self):
        a = app()
        first_entry(a)["bullets"].append({"text": "word " * 600, "source_ids": ["ql1"]})
        errs, notes = shape.check(a)
        self.assertEqual(errs, [])
        self.assertTrue(any("one page" in n for n in notes), notes)
        self.assertFlags(a, "resume body is", max_resume_words=580)
        self.assertEqual(self.errors(EXAMPLE, max_resume_words=580), [])

    def test_word_counts(self):
        resume = {"summary": "**Two** words", "skills": [{"label": "Languages", "items": ["Python", "SQL"]}],
                  "sections": [{"title": "Experience", "entries": [
                      {"heading": "Acme Corp", "subheading": "Engineer", "location": "Remote", "dates": "2020",
                       "link": "https://example.com/not/counted",
                       "bullets": [{"text": "Built **three** things.", "source_ids": ["a1"]}]}]}]}
        self.assertEqual(shape.resume_words(resume), 2 + 3 + 1 + 5 + 3)
        self.assertEqual(shape.letter_words({"paragraphs": ["one two", "three"]}), 3)
        self.assertEqual(shape.resume_words("nonsense"), 0)
        self.assertEqual(shape.letter_words(None), 0)

    def test_final_needs_the_recorded_fact_check(self):
        a = app()
        del a["fact_check"]
        self.assertEqual(self.errors(a), [])
        self.assertFlags(a, "fact_check is missing", final=True)

    def test_final_needs_a_clean_recheck(self):
        a = app()
        del a["recheck"]
        self.assertFlags(a, "recheck is missing", final=True)
        a["recheck"] = {"clean": False, "findings": [
            {"severity": "fabrication", "where": "summary", "problem": "not in the master", "fix": "cut it"}]}
        self.assertEqual(self.errors(a), [])  # work in progress is allowed before the final check
        self.assertFlags(a, "the re-check is not clean", final=True)
        a["recheck"]["clean"] = True
        self.assertFlags(a, "recheck says clean but lists findings")

    def test_recorded_reviews_are_validated_when_present(self):
        a = app()
        a["fact_check"] = {"findings": "none"}
        self.assertFlags(a, "fact_check is missing")
        a = app()
        a["recheck"] = {"clean": "yes", "findings": []}
        self.assertFlags(a, "recheck is missing")
        for bad in ("text", {"severity": "typo", "where": "summary", "problem": "p", "fix": "f"},
                    {"severity": "style", "where": "", "problem": "p", "fix": "f"},
                    {"severity": "style", "where": "summary", "problem": "p"}):
            a = app()
            a["fact_check"]["findings"].append(bad)
            self.assertFlags(a, f"fact_check finding {len(a['fact_check']['findings'])}")

    def test_fact_check_needs_its_two_summary_sentences(self):
        for key in ("keyword_coverage", "length_verdict"):
            a = app()
            del a["fact_check"][key]
            self.assertFlags(a, f"fact_check.{key} is missing")
            a["fact_check"][key] = "  "
            self.assertFlags(a, f"fact_check.{key} is missing", final=True)

    def test_malformed_files_never_crash(self):
        for bad in ({"resume": [], "cover_letter": []}, {"resume": {"sections": [None]}},
                    {"resume": {"skills": [None], "sections": [{"title": "X", "entries": [None]}]}},
                    {"resume": {"sections": [{"title": "X", "entries": [{"heading": "H", "bullets": [None]}]}]}},
                    {"company": 1, "role": 2, "resume": 3, "cover_letter": 4, "fact_check": 5, "recheck": 6}):
            self.assertTrue(self.errors(bad, final=True), bad)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, obj, bom=False):
        path = self.dir / name
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        return str(path)

    def run_script(self, *args):
        p = subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout + p.stderr

    def test_example(self):
        code, out = self.run_script(self.write("t.json", EXAMPLE, bom=True), "--final")
        self.assertEqual(code, 0, out)
        self.assertIn("OK: resume body", out)
        self.assertIn("fact-check recorded and re-check clean", out)

    def test_problems_exit_1(self):
        a = app()
        a["cover_letter"]["paragraphs"] = ["Too short."]
        code, out = self.run_script(self.write("t.json", a))
        self.assertEqual(code, 1, out)
        self.assertIn("cover letter is 2 words", out)

    def test_options(self):
        a = app()
        del a["cover_letter"]
        path = self.write("t.json", a)
        self.assertEqual(self.run_script(path)[0], 1)
        code, out = self.run_script(path, "--resume-only")
        self.assertEqual(code, 0, out)
        self.assertIn("no cover letter", out)
        full = self.write("full.json", EXAMPLE)
        self.assertEqual(self.run_script(full, "--letter-words", "100", "200")[0], 1)
        self.assertEqual(self.run_script(full, "--max-resume-words", "100")[0], 1)
        self.assertEqual(self.run_script(full, "--max-resume-words", "580")[0], 0)

    def test_unreadable_file_exits_2(self):
        self.assertEqual(self.run_script(str(self.dir / "missing.json"))[0], 2)
        self.assertEqual(self.run_script(self.write("broken.json", "{not json"))[0], 2)
        self.assertEqual(self.run_script()[0], 2)

    def test_duplicate_key_is_refused(self):
        code, out = self.run_script(self.write("twice.json", '{"company": "A", "company": "B"}'))
        self.assertEqual(code, 2, out)
        self.assertIn("appears twice", out)

    def test_deeply_nested_file_gives_a_message_not_a_traceback(self):
        code, out = self.run_script(self.write("deep.json", "[" * 100000 + "]" * 100000))
        self.assertEqual(code, 2, out)
        self.assertIn("cannot read", out)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
