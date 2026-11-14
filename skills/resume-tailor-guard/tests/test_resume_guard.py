"""Unit tests for scripts/resume_guard.py.

Run from the skill folder:  python -m unittest discover -s tests
Everything in the fixtures is invented.
"""
import copy
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import resume_guard as guard  # noqa: E402

GUARD = str(ROOT / "scripts" / "resume_guard.py")
EXAMPLES = ROOT / "examples"

MASTER = {
    "confirmed_on": "2026-01-15",
    "contact": {"name": "Sam Rivera", "location": "Austin, TX", "phone": "(512) 555-0117",
                "email": "sam.rivera@example.com"},
    "facts": ["7 years of professional experience in backend engineering."],
    "rewording_notes": ["The 99.9% uptime figure was a target for 2031, never a result."],
    "skills": {"Languages": ["Python", "SQL"], "Tools": ["Docker", "PostgreSQL"]},
    "experience": [
        {"heading": "Brightwater Labs", "subheading": "Software Engineer", "location": "Austin, TX",
         "dates": "Mar 2020 – Jun 2024",
         "bullets": [
             {"id": "bw1", "text": "Cut API latency from 350ms to 120ms for 1,200 daily users."},
             {"id": "bw2", "text": "Mentored three engineers and shipped 4 releases that saved about $1.5 million a year."},
             {"id": "bw3", "text": "Moved file storage to S3 and tracked p95 latency for v2.1 of the service."}]},
        {"heading": "Brightwater Labs", "subheading": "Junior Software Engineer", "location": "Austin, TX",
         "dates": "Jul 2017 – Feb 2020",
         "bullets": [{"id": "bj1", "text": "Wrote 60 integration tests for the billing service."}]},
    ],
    "projects": [
        {"heading": "tidepool", "link": "https://code.example/sam/tidepool", "dates": "2022",
         "bullets": [{"id": "tp1", "text": "Open-source tide prediction library with 25 contributors."}]},
    ],
    "education": [
        {"heading": "Lakeshore University", "subheading": "B.S. in Mathematics", "dates": "2013 – 2017"},
    ],
}

# A clean tailored résumé. It rewords on purpose: 1,200 -> 1200, three -> 3, $1.5 million -> $1.5M,
# an en dash -> a hyphen, SQL -> sql, and it leaves out header fields that the master has.
RESUME = {
    "summary": "Backend engineer with **7 years** of professional experience.",
    "skills": [{"label": "Languages", "items": ["Python", "sql"]}, {"label": "Tools", "items": ["Docker"]}],
    "sections": [
        {"title": "Experience", "entries": [
            {"heading": "Brightwater Labs", "subheading": "Software Engineer", "location": "Austin, TX",
             "dates": "Mar 2020 - Jun 2024",
             "bullets": [
                 {"text": "Cut API latency from 350ms to **120ms** for 1200 daily users.", "source_ids": ["bw1"]},
                 {"text": "Mentored 3 engineers and shipped 4 releases, saving about $1.5M a year.",
                  "source_ids": ["bw2"]}]},
            {"heading": "Brightwater Labs", "subheading": "Junior Software Engineer", "dates": "Jul 2017 – Feb 2020",
             "bullets": [{"text": "Wrote 60 integration tests for the billing service.", "source_ids": ["bj1"]}]}]},
        {"title": "Projects", "entries": [
            {"heading": "tidepool", "link": "https://code.example/sam/tidepool", "dates": "2022",
             "bullets": [{"text": "Open-source tide prediction library with 25 contributors.",
                          "source_ids": ["tp1"]}]}]},
        {"title": "Education", "entries": [
            {"heading": "Lakeshore University", "subheading": "B.S. in Mathematics", "dates": "2013-2017"}]},
    ],
}
LETTER = {"date": "January 20, 2026", "greeting": "Dear Hiring Team,", "paragraphs": ["First.", "Second."],
          "closing": "Sincerely,"}


def master():
    return copy.deepcopy(MASTER)


def resume():
    return copy.deepcopy(RESUME)


def entry(r, section=0, index=0):
    return r["sections"][section]["entries"][index]


class Numbers(unittest.TestCase):
    def test_plain_and_decimal(self):
        self.assertEqual(guard.numbers("3 teams, a 0.870 score and a .5 share"), {"3", "0.87", "0.5"})

    def test_thousands_separator_is_the_same_number(self):
        self.assertEqual(guard.numbers("1,200 users"), guard.numbers("1200 users"))
        self.assertEqual(guard.numbers("$1,200.50 and 12,345,678"), {"1200.5", "12345678"})

    def test_sentence_punctuation(self):
        self.assertEqual(guard.numbers("In 2023, we grew to 40."), {"2023", "40"})

    def test_magnitude_suffix_changes_the_value(self):
        self.assertEqual(guard.numbers("5k"), {"5000"})
        self.assertEqual(guard.numbers("$5M"), {"5000000"})
        self.assertEqual(guard.numbers("2bn and 3B"), {"2000000000", "3000000000"})
        self.assertEqual(guard.numbers("1.5k+"), {"1500"})
        self.assertNotEqual(guard.numbers("5k users"), guard.numbers("5 users"))

    def test_magnitude_words(self):
        self.assertEqual(guard.numbers("5 million"), guard.numbers("$5M"))
        self.assertEqual(guard.numbers("2 thousand"), guard.numbers("2k"))
        self.assertEqual(guard.numbers("1.2 Million events"), {"1200000"})
        self.assertEqual(guard.numbers("3 hundred"), {"300"})

    def test_a_number_with_a_unit_is_read(self):
        self.assertEqual(guard.numbers("200ms, 16GB, 10x, 40%, 5kg, 8MB, 30min, 4h"),
                         {"200", "16", "10", "40", "5", "8", "30", "4"})

    def test_digits_glued_to_a_name_are_not_numbers(self):
        self.assertEqual(guard.numbers("S3, EC2, p95, v2.1, GPT4, k8s and B2B"), set())

    def test_bold_markers_do_not_hide_a_number(self):
        self.assertEqual(guard.numbers("**14** DAGs and **5**k rows"), {"14", "5000"})

    def test_number_words(self):
        self.assertEqual(guard.numbers("two teams, Twelve people, twenty-five sites and ninety days"),
                         {"2", "12", "25", "90"})

    def test_number_words_with_a_magnitude(self):
        self.assertEqual(guard.numbers("three hundred, five thousand and two million"),
                         {"300", "5000", "2000000"})

    def test_one_is_not_counted(self):
        self.assertEqual(guard.numbers("one of the teams, no one, one-on-one"), set())

    def test_ordinals_are_not_counted(self):
        self.assertEqual(guard.numbers("first, second and third place"), set())

    def test_number_words_inside_other_words(self):
        self.assertEqual(guard.numbers("often, tension, attend, seventeenth, tenfold"), set())

    def test_ranges_and_fractions(self):
        self.assertEqual(guard.numbers("10-15 people, 3–4 weeks, 2/3 of cases"), {"10", "15", "3", "4", "2"})

    def test_other_unicode_digits(self):
        self.assertEqual(guard.numbers("１２ teams"), {"12"})

    def test_leading_zeros(self):
        self.assertEqual(guard.numbers("007 and 05/2021"), {"7", "5", "2021"})


class Norm(unittest.TestCase):
    def test_dashes_spacing_and_case_do_not_count(self):
        self.assertEqual(guard.norm("Jun 2021 – Aug 2024"), guard.norm("jun 2021 - aug  2024"))
        self.assertEqual(guard.norm("Jun 2021—Aug 2024"), guard.norm("Jun 2021 - Aug 2024"))

    def test_quotes_do_not_count(self):
        self.assertEqual(guard.norm("Master’s degree"), guard.norm("Master's degree"))

    def test_different_text_stays_different(self):
        self.assertNotEqual(guard.norm("Jun 2021 - Aug 2024"), guard.norm("Jun 2020 - Aug 2024"))
        self.assertNotEqual(guard.norm("Software Engineer"), guard.norm("Senior Software Engineer"))


class Master(unittest.TestCase):
    def test_fixture_is_valid(self):
        self.assertEqual(guard.validate_master(MASTER), [])

    def test_example_master_is_valid(self):
        example = json.loads((EXAMPLES / "master_resume.json").read_text(encoding="utf-8"))
        self.assertEqual(guard.validate_master(example), [])

    def test_must_be_an_object(self):
        self.assertTrue(guard.validate_master([]))

    def test_contact_needs_a_name(self):
        m = master()
        del m["contact"]["name"]
        self.assertTrue(any("contact" in p for p in guard.validate_master(m)))

    def test_duplicate_bullet_id(self):
        m = master()
        m["projects"][0]["bullets"][0]["id"] = "bw1"
        self.assertTrue(any("used twice" in p for p in guard.validate_master(m)))

    def test_bullet_needs_id_and_text(self):
        for bad in ("a plain string", {"text": "no id"}, {"id": "x1"}, {"id": "", "text": "empty id"}):
            m = master()
            m["experience"][0]["bullets"].append(bad)
            self.assertTrue(any("every bullet" in p for p in guard.validate_master(m)), bad)

    def test_entry_needs_a_heading(self):
        m = master()
        del m["education"][0]["heading"]
        self.assertTrue(any("heading" in p for p in guard.validate_master(m)))

    def test_section_must_be_a_list(self):
        m = master()
        m["experience"] = {"heading": "Brightwater Labs"}
        self.assertTrue(any("list of entries" in p for p in guard.validate_master(m)))

    def test_a_list_of_strings_is_not_a_section(self):
        m = master()
        m["languages"] = ["English", "Spanish"]
        self.assertTrue(any("languages" in p for p in guard.validate_master(m)))

    def test_header_fields_must_be_strings(self):
        m = master()
        m["projects"][0]["dates"] = 2022
        self.assertTrue(any("dates must be a string" in p for p in guard.validate_master(m)))

    def test_skills_shapes(self):
        m = master()
        m["skills"] = ["Python", "SQL"]
        self.assertEqual(guard.validate_master(m), [])
        m["skills"] = [{"label": "Languages", "items": ["Python", "SQL"]}]
        self.assertEqual(guard.validate_master(m), [])
        self.assertEqual(guard.master_index(m)[3], {"python", "sql"})
        m["skills"] = "Python, SQL"
        self.assertTrue(any("skills" in p for p in guard.validate_master(m)))
        m["skills"] = {"Languages": ["Python", 3]}
        self.assertTrue(any("skills" in p for p in guard.validate_master(m)))

    def test_facts_and_notes_are_lists_of_strings(self):
        m = master()
        m["facts"] = "7 years"
        self.assertTrue(any("facts" in p for p in guard.validate_master(m)))

    def test_needs_at_least_one_entry(self):
        self.assertTrue(any("no entries" in p for p in guard.validate_master({"contact": {"name": "A B"}})))

    def test_confirmed_on_must_not_be_blank(self):
        m = master()
        m["confirmed_on"] = " "
        self.assertTrue(any("confirmed_on" in p for p in guard.validate_master(m)))

    def test_number_pool_leaves_out_contact_notes_ids_and_links(self):
        m = master()
        m["projects"][0]["link"] = "https://code.example/sam/tidepool/releases/4096"
        pool = guard.master_index(m)[4]
        self.assertIn("7", pool)        # from "facts"
        self.assertIn("2017", pool)     # from an entry's dates
        self.assertIn("1200", pool)     # from a bullet
        for absent in ("512", "555", "117", "99.9", "2031", "4096", "1"):
            self.assertNotIn(absent, pool, absent)


class Check(unittest.TestCase):
    def problems(self, r, m=None):
        return guard.check(m or MASTER, r)

    def assertFlags(self, r, *fragments, m=None):
        found = self.problems(r, m)
        self.assertTrue(found, "the guard found nothing")
        for fragment in fragments:
            self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_clean_resume_passes_with_legitimate_rewording(self):
        self.assertEqual(self.problems(RESUME), [])

    def test_application_wrapper_and_bare_resume(self):
        self.assertEqual(self.problems({"company": "X", "resume": resume(), "cover_letter": LETTER}), [])
        self.assertTrue(self.problems({"resume": "not an object"}))

    def test_example_passes(self):
        example_master = json.loads((EXAMPLES / "master_resume.json").read_text(encoding="utf-8"))
        example = json.loads((EXAMPLES / "tailored.json").read_text(encoding="utf-8"))
        self.assertEqual(guard.check(example_master, example), [])

    # --- numbers

    def test_invented_number_in_a_bullet(self):
        r = resume()
        entry(r)["bullets"][0]["text"] = "Cut API latency from 350ms to 90ms for 1200 daily users."
        self.assertFlags(r, "numbers ['90'] are not in the cited master bullets ['bw1']")

    def test_number_from_a_bullet_that_is_not_cited(self):
        r = resume()
        entry(r)["bullets"][0]["text"] = "Cut API latency to 120ms and shipped 4 releases."  # 4 is in bw2
        self.assertFlags(r, "'4'")

    def test_merged_bullet_may_use_numbers_from_all_its_sources(self):
        r = resume()
        entry(r)["bullets"][0] = {"text": "Cut API latency to 120ms and shipped 4 releases.",
                                  "source_ids": ["bw1", "bw2"]}
        self.assertEqual(self.problems(r), [])

    def test_magnitude_inflation(self):
        r = resume()
        entry(r)["bullets"][1]["text"] = "Mentored 3 engineers and shipped 4 releases, saving about $1.5B a year."
        self.assertFlags(r, "'1.5B'")
        r = resume()
        entry(r)["bullets"][0]["text"] = "Cut API latency from 350ms to 120ms for 1200k daily users."
        self.assertFlags(r, "'1200k'")

    def test_invented_number_written_as_a_word(self):
        r = resume()
        entry(r)["bullets"][1]["text"] = "Mentored twelve engineers and shipped 4 releases."
        self.assertFlags(r, "'twelve'")

    def test_number_glued_to_a_unit_is_still_checked(self):
        r = resume()
        entry(r)["bullets"][0]["text"] = "Cut API latency from 350ms to 80ms."  # the master says 120ms
        self.assertFlags(r, "numbers ['80']")

    def test_summary_number_must_be_somewhere_in_the_master(self):
        r = resume()
        r["summary"] = "Backend engineer with 9 years of experience."
        self.assertFlags(r, "summary", "'9'")
        r["summary"] = "Backend engineer who shipped 4 releases and wrote 60 integration tests."
        self.assertEqual(self.problems(r), [])

    def test_summary_cannot_borrow_numbers_from_the_contact_block_or_the_notes(self):
        for text in ("Serves 512 customers.", "Reached 99.9% uptime."):
            r = resume()
            r["summary"] = text
            self.assertFlags(r, "summary")

    def test_section_titles_and_skill_labels_are_checked_for_numbers(self):
        r = resume()
        r["sections"][0]["title"] = "Experience (10+ years)"
        r["skills"][0]["label"] = "Languages (15 years)"
        self.assertFlags(r, "section title", "skills label")

    # --- skills

    def test_invented_skill(self):
        r = resume()
        r["skills"][1]["items"].append("Kubernetes")
        self.assertFlags(r, "skill not in the master skills: 'Kubernetes'")

    def test_skill_must_be_a_string(self):
        r = resume()
        r["skills"][0]["items"].append(42)
        self.assertFlags(r, "skill not in the master skills")

    # --- entry headers

    def test_changed_date(self):
        r = resume()
        entry(r)["dates"] = "Mar 2019 – Jun 2024"
        self.assertFlags(r, "Brightwater Labs: dates", "differs from the master entry")

    def test_date_taken_from_another_entry(self):
        r = resume()
        entry(r)["dates"] = "Jul 2017 – Feb 2020"  # the junior role's dates on the later role
        self.assertFlags(r, "Brightwater Labs: dates")

    def test_changed_title(self):
        r = resume()
        entry(r)["subheading"] = "Senior Software Engineer"
        self.assertFlags(r, "subheading 'Senior Software Engineer' differs from the master entry ('Software Engineer')")

    def test_changed_employer(self):
        r = resume()
        entry(r)["heading"] = "Brightwater Labs International"
        self.assertFlags(r, "no master entry has this heading")

    def test_changed_location_and_link(self):
        r = resume()
        entry(r)["location"] = "New York, NY"
        entry(r, 1)["link"] = "https://code.example/sam/tidepool-pro"
        self.assertFlags(r, "location", "link")

    def test_link_must_match_exactly(self):
        r = resume()
        entry(r, 1)["link"] = "https://code.example/Sam/Tidepool"
        self.assertFlags(r, "link")

    def test_header_field_the_master_does_not_have(self):
        r = resume()
        entry(r, 2)["location"] = "Chicago, IL"
        self.assertFlags(r, "which has no location")

    def test_header_fields_may_be_left_out(self):
        r = resume()
        r["sections"][0]["entries"][0] = {"heading": "Brightwater Labs", "subheading": "Software Engineer",
                                          "bullets": entry(r)["bullets"]}
        self.assertEqual(self.problems(r), [])

    def test_two_roles_at_one_employer_are_told_apart_by_their_bullets(self):
        r = resume()
        r["sections"][0]["entries"] = [{"heading": "Brightwater Labs",
                                        "bullets": [{"text": "Wrote 60 integration tests.", "source_ids": ["bj1"]}]}]
        self.assertEqual(self.problems(r), [])

    def test_header_fields_must_be_strings(self):
        r = resume()
        entry(r)["dates"] = 2024
        self.assertFlags(r, "dates must be a string")

    # --- bullets

    def test_bullet_from_another_entry(self):
        r = resume()
        entry(r)["bullets"].append({"text": "Wrote 60 integration tests.", "source_ids": ["bj1"]})
        self.assertFlags(r, "from another master entry (Brightwater Labs)")
        r = resume()
        entry(r)["bullets"].append({"text": "Built a library with 25 contributors.", "source_ids": ["tp1"]})
        self.assertFlags(r, "from another master entry (tidepool)")

    def test_bullet_without_source_ids(self):
        for ids in (None, [], "bw1", [7]):
            r = resume()
            entry(r)["bullets"][0] = {"text": "Cut API latency.", "source_ids": ids}
            self.assertFlags(r, "bullet without source_ids")
        r = resume()
        del entry(r)["bullets"][0]["source_ids"]
        self.assertFlags(r, "bullet without source_ids")

    def test_bullet_with_an_unknown_id(self):
        r = resume()
        entry(r)["bullets"][0]["source_ids"] = ["bw1", "zz9"]
        self.assertFlags(r, "not in the master ['zz9']")

    def test_bullet_must_be_an_object(self):
        r = resume()
        entry(r)["bullets"].append("Cut costs by 30%.")
        self.assertFlags(r, "every bullet must be an object")

    # --- nothing rides along

    def test_unknown_fields_are_rejected(self):
        r = resume()
        r["headline"] = "Staff Engineer"
        entry(r)["note"] = "Managed 12 people"
        entry(r)["bullets"][0]["evidence"] = "trust me"
        r["skills"][0]["level"] = "expert"
        r["sections"][0]["subtitle"] = "10 years"
        found = self.problems(r)
        for kind in ("resume", "entry", "bullet", "skills group", "section"):
            self.assertTrue(any(f"unknown {kind} field" in p for p in found), kind)

    # --- contact

    def test_contact_block(self):
        r = resume()
        r["contact"] = dict(MASTER["contact"])
        self.assertEqual(self.problems(r), [])
        r["contact"]["email"] = "someone.else@example.com"
        self.assertFlags(r, "contact field 'email' differs from the master")
        r["contact"] = {"name": "Sam Rivera", "title": "Staff Engineer"}
        self.assertFlags(r, "contact field 'title'")
        r["contact"] = "Sam Rivera"
        self.assertFlags(r, "contact must be an object")

    # --- malformed input never crashes

    def test_malformed_tailored_files(self):
        for bad in ([], "text", None, {"summary": 5}, {"skills": "Python"}, {"sections": {}},
                    {"sections": ["Experience"]}, {"sections": [{"title": "X", "entries": ["text"]}]},
                    {"sections": [{"title": "X", "entries": [{"heading": "tidepool", "bullets": "none"}]}]},
                    {"skills": [["Python"]]}, {"skills": [{"label": 3, "items": ["Python"]}]}):
            self.assertTrue(self.problems(bad), bad)

    def test_unusable_master_raises(self):
        with self.assertRaises(ValueError):
            guard.check({"contact": {}}, RESUME)


class Markdown(unittest.TestCase):
    def test_resume_layout(self):
        text = guard.resume_markdown(MASTER, RESUME)
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Sam Rivera")
        self.assertEqual(lines[2], "Austin, TX | (512) 555-0117 | sam.rivera@example.com")
        self.assertIn("- **Languages:** Python, sql", lines)
        self.assertIn("### Brightwater Labs | Austin, TX", lines)
        self.assertIn("*Software Engineer* | Mar 2020 - Jun 2024", lines)
        self.assertIn("### [tidepool](https://code.example/sam/tidepool) | 2022", lines)
        self.assertIn("- Cut API latency from 350ms to **120ms** for 1200 daily users.", lines)
        self.assertTrue(text.endswith("*B.S. in Mathematics* | 2013-2017\n"))

    def test_contact_block_always_comes_from_the_master(self):
        r = resume()
        r["contact"] = {"name": "Someone Else", "email": "other@example.com"}
        text = guard.resume_markdown(MASTER, r)
        self.assertIn("sam.rivera@example.com", text)
        self.assertNotIn("Someone Else", text)

    def test_letter(self):
        text = guard.letter_markdown(MASTER, LETTER)
        self.assertTrue(text.startswith("# Sam Rivera\n"))
        self.assertIn("\nJanuary 20, 2026\n\nDear Hiring Team,\n\nFirst.\n\nSecond.\n\nSincerely,\n\nSam Rivera\n", text)

    def test_a_line_break_inside_a_field_cannot_add_markdown_structure(self):
        r = resume()
        entry(r)["bullets"][0]["text"] = "Cut API latency.\n\n## Awards\n- Engineer of the year"
        text = guard.resume_markdown(MASTER, r)
        self.assertIn("- Cut API latency. ## Awards - Engineer of the year", text.splitlines())
        self.assertNotIn("## Awards", text.splitlines())

    def test_malformed_letter_is_not_rendered(self):
        for bad in (None, {}, dict(LETTER, paragraphs=[]), dict(LETTER, paragraphs="text"), dict(LETTER, date="")):
            self.assertIsNone(guard.letter_markdown(MASTER, bad))


class Cli(unittest.TestCase):
    """Helpers for running the script as a program."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, obj, bom=False):
        path = self.dir / name
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        return str(path)

    def run_guard(self, *args):
        p = subprocess.run([sys.executable, "-B", GUARD, *args], capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout + p.stderr


class CommandLine(Cli):
    def test_master_alone(self):
        code, out = self.run_guard(self.write("m.json", MASTER))
        self.assertEqual(code, 0, out)
        self.assertIn("master OK: 3 sections, 4 entries, 5 bullets, 4 skills", out)
        self.assertIn("confirmed by the candidate on 2026-01-15", out)

    def test_master_alone_not_confirmed_yet(self):
        m = master()
        del m["confirmed_on"]
        code, out = self.run_guard(self.write("m.json", m))
        self.assertEqual(code, 0, out)
        self.assertIn("NOT CONFIRMED YET", out)

    def test_master_alone_with_problems(self):
        m = master()
        m["projects"][0]["bullets"][0]["id"] = "bw1"
        code, out = self.run_guard(self.write("m.json", m))
        self.assertEqual(code, 1, out)
        self.assertIn("used twice", out)

    def test_clean_resume(self):
        code, out = self.run_guard(self.write("m.json", MASTER), self.write("t.json", {"resume": RESUME}))
        self.assertEqual(code, 0, out)
        self.assertIn("OK:", out)
        self.assertIn("Not checked:", out)

    def test_fabrication_exits_1(self):
        r = resume()
        r["skills"][0]["items"].append("Rust")
        code, out = self.run_guard(self.write("m.json", MASTER), self.write("t.json", r))
        self.assertEqual(code, 1, out)
        self.assertIn("skill not in the master skills: 'Rust'", out)
        self.assertIn("Never edit the master", out)

    def test_unconfirmed_master_is_refused(self):
        m = master()
        del m["confirmed_on"]
        code, out = self.run_guard(self.write("m.json", m), self.write("t.json", RESUME))
        self.assertEqual(code, 2, out)
        self.assertIn("confirmed_on", out)

    def test_unusable_master_is_refused(self):
        code, out = self.run_guard(self.write("m.json", {"contact": {"name": "A B"}}), self.write("t.json", RESUME))
        self.assertEqual(code, 2, out)
        self.assertIn("not usable", out)

    def test_unreadable_files(self):
        m = self.write("m.json", MASTER)
        self.assertEqual(self.run_guard(m, str(self.dir / "missing.json"))[0], 2)
        self.assertEqual(self.run_guard(m, self.write("broken.json", "{not json"))[0], 2)
        self.assertEqual(self.run_guard(str(self.dir / "missing.json"))[0], 2)
        self.assertEqual(self.run_guard()[0], 2)

    def test_file_with_a_byte_order_mark(self):
        code, out = self.run_guard(self.write("m.json", MASTER, bom=True), self.write("t.json", RESUME, bom=True))
        self.assertEqual(code, 0, out)

    def test_markdown_is_written_only_when_the_check_passes(self):
        m = self.write("m.json", MASTER)
        bad = resume()
        entry(bad)["dates"] = "Mar 2012 – Jun 2024"
        code, out = self.run_guard(m, self.write("bad.json", bad), "--markdown", str(self.dir / "md"))
        self.assertEqual(code, 1, out)
        self.assertFalse((self.dir / "md").exists())
        good = {"company": "X", "role": "Y", "resume": RESUME, "cover_letter": LETTER}
        code, out = self.run_guard(m, self.write("good.json", good), "--markdown", str(self.dir / "md"))
        self.assertEqual(code, 0, out)
        self.assertTrue((self.dir / "md" / "resume.md").read_text(encoding="utf-8").startswith("# Sam Rivera\n"))
        self.assertIn("Dear Hiring Team,", (self.dir / "md" / "cover_letter.md").read_text(encoding="utf-8"))

    def test_markdown_refuses_a_malformed_letter(self):
        app = {"resume": RESUME, "cover_letter": {"paragraphs": "one long string"}}
        code, out = self.run_guard(self.write("m.json", MASTER), self.write("t.json", app),
                                   "--markdown", str(self.dir / "md"))
        self.assertEqual(code, 1, out)
        self.assertFalse((self.dir / "md").exists())

    def test_markdown_needs_a_tailored_file(self):
        self.assertEqual(self.run_guard(self.write("m.json", MASTER), "--markdown", str(self.dir / "md"))[0], 2)

    def test_example_output_is_what_the_guard_writes(self):
        code, out = self.run_guard(str(EXAMPLES / "master_resume.json"), str(EXAMPLES / "tailored.json"),
                                   "--markdown", str(self.dir / "md"))
        self.assertEqual(code, 0, out)
        for name in ("resume.md", "cover_letter.md"):
            self.assertEqual((self.dir / "md" / name).read_text(encoding="utf-8"),
                             (EXAMPLES / "output" / name).read_text(encoding="utf-8"), name)


ZWSP, NBSP, NNBSP, SHY = chr(0x200B), chr(0xA0), chr(0x202F), chr(0xAD)  # invisible or odd spaces, by code point


class Magnitudes(unittest.TestCase):
    """A magnitude must never get lost, however it is attached to its number."""

    def test_every_way_of_writing_fourteen_thousand(self):
        forms = ["14 thousand", "14-thousand", "14  thousand", "14\tthousand", "14" + NBSP + NBSP + "thousand",
                 "14+ thousand", "*14* thousand", "**14** thousand", "_14_ thousand", "`14` thousand",
                 "14" + ZWSP + "thousand", "14 " + ZWSP + "thousand", "14 thousands", "14k", "14K", "14 k", "14 K",
                 "14,000", "14 000", "14" + NNBSP + "000", "14'000", "1.4e4", "14e3", "fourteen thousand",
                 "Fourteen-thousand", "fourteen  thousand"]
        for text in forms:
            self.assertEqual(guard.numbers(text + " DAGs"), {"14000"}, repr(text))

    def test_other_magnitude_spellings(self):
        cases = (("6 K", "6000"), ("$14MM", "14000000"), ("$14 MM", "14000000"), ("14 mn", "14000000"),
                 ("14 mil", "14000000"), ("14 mio", "14000000"), ("14 mln", "14000000"), ("3 bln", "3000000000"),
                 ("14 lakh", "1400000"), ("2 crore", "20000000"), ("14e6", "14000000"), ("2.5e3", "2500"),
                 ("14e-3", "0.014"), ("14 hundred thousand", "1400000"), ("three hundred thousand", "300000"),
                 ("a 5-million-row table", "5000000"), ("6+ million", "6000000"), ("one hundred", "100"),
                 ("a million", "1000000"), ("an hundred", "100"), ("One Million", "1000000"),
                 ("a hundred thousand", "100000"), ("half a million", "1000000"))
        for text, value in cases:
            self.assertEqual(guard.numbers(text), {value}, text)

    def test_millimetres_are_not_millions(self):
        self.assertEqual(guard.numbers("5mm and 5 mm"), {"5"})
        self.assertEqual(guard.numbers("5MM"), {"5000000"})

    def test_units_that_start_like_a_magnitude(self):
        self.assertEqual(guard.numbers("95 minutes, 5 miles, 3 months, 12 km, 8 MB, 23 bad loads, 4 kB, 9 bytes"),
                         {"95", "5", "3", "12", "8", "23", "4", "9"})

    def test_a_magnitude_word_without_a_number_is_a_token_of_its_own(self):
        self.assertEqual(guard.numbers("hundreds of users"), {"hundreds"})
        self.assertEqual(guard.numbers("Thousands of users and a dozen teams"), {"thousands", "dozen"})
        self.assertEqual(guard.numbers("14 (thousand)"), {"14", "thousand"})
        self.assertEqual(guard.numbers("14 dozen"), {"14", "dozen"})
        self.assertEqual(guard.numbers("a multi-million-dollar budget"), {"million"})

    def test_a_point_before_three_digits_is_ambiguous(self):
        self.assertEqual(guard.numbers("14.000"), {"14.000"})  # fourteen, or fourteen thousand
        self.assertNotEqual(guard.numbers("14.000"), guard.numbers("14"))
        self.assertNotEqual(guard.numbers("14.000"), guard.numbers("14,000"))
        self.assertEqual(guard.numbers("1.234.567"), {"1234567"})
        self.assertEqual(guard.numbers("0.875 and 3.142 and 2.50"), {"0.875", "3.142", "2.5"})

    def test_number_after_x_or_after_an_abbreviation(self):
        self.assertEqual(guard.numbers("x10 faster, No.5 and approx.40%"), {"10", "5", "40"})

    def test_invisible_characters_do_not_split_a_number(self):
        self.assertEqual(guard.numbers("4" + ZWSP + "1 DAGs"), {"41"})
        self.assertEqual(guard.numbers("4" + SHY + "1 DAGs"), {"41"})

    def test_what_is_not_read(self):  # the README lists these as limits
        self.assertEqual(guard.numbers("doubled throughput with zero downtime, twice as fast at half the cost"), set())
        self.assertEqual(guard.numbers("one hundred and twenty"), {"100", "20"})


class Hardening(unittest.TestCase):
    """Checks added after the review: magnitudes in bullets, labels, titles, the confirmation date, master fields."""

    def problems(self, r, m=None):
        return guard.check(m or MASTER, r)

    def assertFlags(self, r, *fragments, m=None):
        found = self.problems(r, m)
        self.assertTrue(found, "the guard found nothing")
        for fragment in fragments:
            self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_inflated_magnitude_in_a_bullet(self):  # the master says "shipped 4 releases"
        for claim in ("4-thousand", "4+ million", "4  hundred", "*4* thousand", "4" + ZWSP + " thousand",
                      "4" + NBSP + NBSP + "million", "4 (thousand)", "4 K", "4 k", "4MM", "4 mn", "4 mil", "4.000",
                      "4e3", "4 dozen", "a hundred", "one thousand", "thousands of", "4 thousands", "4 lakh"):
            r = resume()
            entry(r)["bullets"][1] = {"text": f"Mentored 3 engineers and shipped {claim} releases.",
                                      "source_ids": ["bw2"]}
            self.assertTrue(self.problems(r), claim)

    def test_a_bare_magnitude_word_passes_only_when_the_cited_bullet_has_it(self):
        m = master()
        m["experience"][0]["bullets"].append({"id": "bw4", "text": "Supported hundreds of clinics."})
        r = resume()
        entry(r)["bullets"].append({"text": "Supported hundreds of clinics.", "source_ids": ["bw4"]})
        self.assertEqual(self.problems(r, m), [])
        entry(r)["bullets"][-1]["text"] = "Supported thousands of clinics."
        self.assertFlags(r, "numbers ['thousands']", m=m)
        entry(r)["bullets"][-1] = {"text": "Saved millions a year.", "source_ids": ["bw2"]}  # bw2: $1.5 million
        self.assertFlags(r, "numbers ['millions']", m=m)

    def test_skill_label_must_use_the_master_s_own_words(self):
        for label in ("Kubernetes, Terraform and Apache Spark", "Streaming (Kafka, Kinesis)", "Expert languages",
                      "Languages (fluent)", "Languages (15 years)"):
            r = resume()
            r["skills"][0]["label"] = label
            self.assertFlags(r, "skills label")
        for label in ("Languages", "Language", "languages and TOOLS", "Other tools", "Python and SQL", "Core skills"):
            r = resume()
            r["skills"][0]["label"] = label
            self.assertEqual(self.problems(r), [], label)

    def test_section_title_must_use_the_master_s_section_names(self):
        for title in ("Senior Leadership Experience", "Experience at Brightwater", "Experience (10+ years)"):
            r = resume()
            r["sections"][0]["title"] = title
            self.assertFlags(r, "section title")
        r = resume()
        r["sections"][0]["title"] = "Selected Work"
        self.assertFlags(r, "section title 'Selected Work' does not name a master section")
        for title in ("Experience", "EXPERIENCE", "Professional Experience", "Relevant work experience"):
            r = resume()
            r["sections"][0]["title"] = title
            self.assertEqual(self.problems(r), [], title)
        r = resume()
        r["sections"][1]["title"] = "Selected Project"
        self.assertEqual(self.problems(r), [])

    def test_entry_stays_under_a_title_that_names_its_master_section(self):
        r = resume()
        project = r["sections"].pop(1)["entries"][0]
        r["sections"][0]["entries"].append(project)  # a personal project shown as work experience
        self.assertFlags(r, "tidepool: this entry is in the master's 'projects' section, but it is shown under "
                            "'Experience'")
        r["sections"][0]["title"] = "Experience and Projects"
        self.assertEqual(self.problems(r), [])

    def test_confirmed_on_must_be_a_real_date_that_is_not_in_the_future(self):
        for value in ("TBD", "unconfirmed", "no", "YYYY-MM-DD", "2099-01-01", "not yet - waiting for candidate",
                      "2026-13-01", "02/11/2026", "20260115", "2026-1-5", " ", "", None, False, 20260115):
            m = master()
            m["confirmed_on"] = value
            self.assertIsNone(guard.confirmed_on(m), repr(value))
            self.assertTrue(any("confirmed_on" in p for p in guard.validate_master(m)), repr(value))
        today = datetime.date.today()
        for day in (today, today + datetime.timedelta(days=1), datetime.date(2020, 2, 29)):
            m = master()
            m["confirmed_on"] = day.isoformat()
            self.assertEqual(guard.confirmed_on(m), day)
            self.assertEqual(guard.validate_master(m), [])
        m["confirmed_on"] = (today + datetime.timedelta(days=2)).isoformat()
        self.assertIsNone(guard.confirmed_on(m))
        m = master()
        del m["confirmed_on"]
        self.assertIsNone(guard.confirmed_on(m))
        self.assertEqual(guard.validate_master(m), [])  # a missing date is "not confirmed yet", not a broken file

    def test_unknown_master_fields_are_rejected(self):
        m = master()
        m["education"][0]["gpa"] = "3.97"
        self.assertTrue(any("unknown master entry field(s) ['gpa']" in p and "bullet" in p
                            for p in guard.validate_master(m)))
        m = master()
        m["experience"][0]["bullets"][0]["source"] = "an old resume"
        self.assertTrue(any("unknown master bullet field(s) ['source']" in p for p in guard.validate_master(m)))
        m = master()
        m["headline"] = "Staff engineer with 20 years of experience"
        self.assertTrue(any("unknown field 'headline'" in p for p in guard.validate_master(m)))

    def test_underscore_fields_in_the_master_are_comments(self):
        m = master()
        m["_note"] = "fictional, 777 words"
        m["experience"][0]["_comment"] = "checked 888 times"
        m["experience"][0]["bullets"][0]["_c"] = "999"
        self.assertEqual(guard.validate_master(m), [])
        self.assertFalse({"777", "888", "999"} & guard.master_index(m)[4])

    def test_summary_cannot_open_a_heading_a_list_or_a_quote(self):
        cases = (("### Somewhere | Staff Engineer", "\\### Somewhere | Staff Engineer"),
                 ("- a list item", "\\- a list item"), ("> a quote", "\\> a quote"),
                 ("1. a numbered item", "1\\. a numbered item"),
                 ("**7 years** of experience.", "**7 years** of experience."),
                 ("3.5 years of experience.", "3.5 years of experience."))
        for summary, line in cases:
            r = resume()
            r["summary"] = summary
            self.assertIn(line, guard.resume_markdown(MASTER, r).splitlines(), summary)
        letter = dict(LETTER, paragraphs=["# Not a heading", "Second."])
        self.assertIn("\\# Not a heading", guard.letter_markdown(MASTER, letter).splitlines())


class CommandLineHardening(Cli):
    """Program behaviour added after the review."""

    def test_duplicate_keys_are_refused(self):
        twice = json.dumps(MASTER)[:-1] + ', "experience": []}'
        code, out = self.run_guard(self.write("m.json", twice))
        self.assertEqual(code, 2, out)
        self.assertIn("'experience' appears twice", out)
        tailored = '{"summary": "Invented 99 things.", "summary": "Backend engineer."}'
        code, out = self.run_guard(self.write("ok.json", MASTER), self.write("t.json", tailored))
        self.assertEqual(code, 2, out)
        self.assertIn("'summary' appears twice", out)

    def test_deeply_nested_file_gives_a_message_not_a_traceback(self):
        deep = self.write("deep.json", "[" * 100000 + "]" * 100000)
        for args in ((deep,), (self.write("m.json", MASTER), deep)):
            code, out = self.run_guard(*args)
            self.assertEqual(code, 2, out)
            self.assertIn("cannot read", out)
            self.assertNotIn("Traceback", out)

    def test_markdown_that_cannot_be_written(self):
        m, t = self.write("m.json", MASTER), self.write("t.json", {"resume": RESUME})
        code, out = self.run_guard(m, t, "--markdown", self.write("afile", "this is a file, not a folder"))
        self.assertEqual(code, 2, out)
        self.assertIn("could not be written", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("OK:", out)
        (self.dir / "md" / "resume.md").mkdir(parents=True)  # the output file cannot be opened for writing
        code, out = self.run_guard(m, t, "--markdown", str(self.dir / "md"))
        self.assertEqual(code, 2, out)
        self.assertNotIn("Traceback", out)

    def test_note_about_a_cover_letter_from_an_earlier_run(self):
        m, out_dir = self.write("m.json", MASTER), str(self.dir / "md")
        with_letter = {"company": "X", "role": "Y", "resume": RESUME, "cover_letter": LETTER}
        self.assertEqual(self.run_guard(m, self.write("a.json", with_letter), "--markdown", out_dir)[0], 0)
        code, out = self.run_guard(m, self.write("b.json", {"resume": RESUME}), "--markdown", out_dir)
        self.assertEqual(code, 0, out)
        self.assertIn("cover_letter.md is from an earlier run and was not updated", out)

    def test_placeholder_confirmation_is_refused(self):
        for value in ("TBD", "unconfirmed", "2099-01-01"):
            m = master()
            m["confirmed_on"] = value
            path = self.write("m.json", m)
            code, out = self.run_guard(path)
            self.assertEqual(code, 1, out)
            self.assertNotIn("confirmed by the candidate", out)
            code, out = self.run_guard(path, self.write("t.json", RESUME))
            self.assertEqual(code, 2, out)
            self.assertNotIn("OK:", out)

    def test_ok_line_names_what_was_checked(self):
        code, out = self.run_guard(self.write("m.json", MASTER), self.write("t.json", RESUME))
        self.assertEqual(code, 0, out)
        self.assertIn("OK: no unsupported numbers, listed skills, labels, entry headers or contact details", out)


if __name__ == "__main__":
    unittest.main()
