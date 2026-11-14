"""Unit tests for scripts/style_check.py (the optional style step).

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
import style_check as style  # noqa: E402

SCRIPT = str(ROOT / "scripts" / "style_check.py")
EXAMPLE = json.loads((ROOT / "examples" / "tailored.json").read_text(encoding="utf-8"))

BAD = [
    "I built the service — and it held up.",
    "It ran well – and fast.",
    "I shipped it – then fixed it.",
    "Loading was easy; testing was hard.",
    "We shipped it -- finally.",
    "I leveraged Airflow for scheduling.",
    "The project showcases my skills.",
    "I am passionate about data quality.",
    "It serves as a staging area.",
    "I want to harness streaming data for dispatch.",
    "My work fosters collaboration between teams.",
    "This was not only fast but also cheap.",
    "This is not just a script but a platform.",
    "It's not a script, it's a platform.",
    "From startups to hospitals, data is everywhere.",
    "I don't have experience with Kubernetes.",
    "Unfortunately I have not used Spark.",
    "I am not familiar with Go.",
    "I have limited experience with Rust.",
    "Great question, I hope this helps.",
    "The launch went well \U0001F680.",
    " ".join(["word"] * 45) + ".",
]
GOOD = [
    "I built a test harness that replays a day of carrier events.",
    "The pilot ran from Jan 2023 to Mar 2023, and the data covers 2019–2023.",
    "From 2019 to 2021 I worked on the statements system.",
    "Before that I volunteered in foster care for two years.",
    "Kubernetes builds on the same Docker workflow I already use.",
    "From what I read in the posting, the team needs someone to own the nightly loads.",
    "The posted range is $118k–$142k and I worked there 2021–present, mostly Jan–Mar.",
    "I cut the load window from 4 hours to 2.5 hours.",
    "The pilot was not moved to production.",
    "The closest thing I have done is a three-month pilot.",
    "Data engineer with **5 years** of professional experience.",
]


class Text(unittest.TestCase):
    def test_bad_sentences_are_flagged(self):
        for text in BAD:
            self.assertTrue(style.check_text("t", text), text[:70])

    def test_good_sentences_pass(self):
        for text in GOOD:
            self.assertEqual(style.check_text("t", text), [], text[:70])

    def test_each_problem_names_where_it_is(self):
        problems = style.check_text("cover letter paragraph 2", "I am thrilled; truly.")
        self.assertEqual(len(problems), 2)
        self.assertTrue(all(p.startswith("cover letter paragraph 2: ") for p in problems))

    def test_the_employer_name_is_exempt(self):
        text = "I admire how Seamless Freight handles billing."
        self.assertTrue(style.check_text("t", text))
        self.assertEqual(style.check_text("t", text, "Seamless Freight"), [])

    def test_the_employer_name_is_exempt_only_as_a_whole_word(self):
        text = "I am passionate about seamless pipelines."
        self.assertEqual(len(style.check_text("t", text, "On")), 2)  # "on" inside "passionate" is not the employer
        self.assertEqual(len(style.check_text("t", text, "Seam")), 2)
        self.assertEqual(len(style.check_text("t", text, "  ")), 2)

    def test_sentence_length_limit(self):
        self.assertEqual(style.check_text("t", " ".join(["word"] * 40) + "."), [])
        self.assertTrue(style.check_text("t", " ".join(["word"] * 41) + "."))

    def test_empty_text(self):
        self.assertEqual(style.check_text("t", ""), [])
        self.assertEqual(style.check_text("t", None), [])


class Application(unittest.TestCase):
    def test_example_passes(self):
        self.assertEqual(style.check_application(EXAMPLE), [])

    def test_summary_and_each_paragraph_are_checked(self):
        app = copy.deepcopy(EXAMPLE)
        app["resume"]["summary"] = "Passionate data engineer."
        app["cover_letter"]["paragraphs"][2] += " I leveraged Kafka."
        problems = style.check_application(app)
        self.assertTrue(any(p.startswith("resume summary: ") for p in problems), problems)
        self.assertTrue(any(p.startswith("cover letter paragraph 3: ") for p in problems), problems)

    def test_bullets_are_not_checked(self):
        app = copy.deepcopy(EXAMPLE)
        app["resume"]["sections"][0]["entries"][0]["bullets"][0]["text"] = "Leveraged Airflow; spearheaded dbt."
        self.assertEqual(style.check_application(app), [])

    def test_company_comes_from_the_file_unless_given(self):
        app = {"company": "Pivotal Freight", "resume": {"summary": "Wants to join Pivotal Freight."}}
        self.assertEqual(style.check_application(app), [])
        self.assertTrue(style.check_application(app, company=""))

    def test_malformed_files_never_crash(self):
        for bad in ([], {}, {"resume": 3, "cover_letter": 4}, {"company": 5, "resume": {"summary": 6},
                                                                "cover_letter": {"paragraphs": [7, None]}}):
            self.assertIsInstance(style.check_application(bad), list)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, obj):
        path = self.dir / name
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def run_script(self, *args):
        p = subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout + p.stderr

    def test_exit_codes(self):
        code, out = self.run_script(self.write("ok.json", EXAMPLE))
        self.assertEqual(code, 0, out)
        app = copy.deepcopy(EXAMPLE)
        app["cover_letter"]["paragraphs"][0] += " I am passionate about freight — truly."
        code, out = self.run_script(self.write("bad.json", app))
        self.assertEqual(code, 1, out)
        self.assertIn("an em dash", out)
        self.assertIn("'passionate'", out)
        self.assertEqual(self.run_script(str(self.dir / "missing.json"))[0], 2)

    def test_files_that_cannot_be_read(self):
        code, out = self.run_script(self.write("twice.json", '{"company": "A", "company": "B"}'))
        self.assertEqual(code, 2, out)
        self.assertIn("appears twice", out)
        code, out = self.run_script(self.write("deep.json", "[" * 100000 + "]" * 100000))
        self.assertEqual(code, 2, out)
        self.assertNotIn("Traceback", out)

    def test_company_option(self):
        app = {"company": "", "resume": {"summary": "Wants to join Pivotal Freight."}}
        path = self.write("t.json", app)
        self.assertEqual(self.run_script(path)[0], 1)
        self.assertEqual(self.run_script(path, "--company", "Pivotal Freight")[0], 0)


if __name__ == "__main__":
    unittest.main()
