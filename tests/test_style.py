"""Checks for style_guard.py (the mechanical half of the writing rules). Run: python tests/test_style.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import style_guard  # noqa: E402

BAD = [
    "I built it — and it worked.",
    "It ran well – and fast.",
    "Retrieval was easy; grounding was hard.",
    "We shipped it -- finally.",
    "I leveraged ChromaDB for search.",
    "The project showcases my skills.",
    "I am passionate about evaluation.",
    "It serves as a test bed.",
    "I want to harness LLMs for hospitals.",
    "This was not only fast but also accurate.",
    "It's not a demo, it's a product.",
    "From startups to hospitals, AI is everywhere.",
    "I don't have experience with Kubernetes.",
    "Unfortunately I have not used Spark.",
    "I am not familiar with Go.",
    "I have limited experience with Rust.",
    "Great question, I hope this helps.",
    " ".join(["word"] * 45) + ".",
]
GOOD = [
    "I built an evaluation harness that measures retrieval hit rate.",
    "It reached a 0.87 retrieval hit rate over a 70-page report.",
    "The study ran from Mar 2021 to the present, and the data covers 2015–2019.",
    "From 2016 to 2018 I led three analysts.",
    "The office is in Foster City.",
    "Kubernetes builds on the same Docker workflow I already use.",
    "The eval harness that I wrote runs in CI.",
    "I built the evaluation dataset and harness.",
    "From what I read in the posting, the team needs someone to own evaluation.",
    "From your posting, it sounds like you want someone to own the eval loop.",
    "The posted range is $140k–$150k and I worked on it 2021–present, mostly Jan–Mar.",
]
BAD += ["From startups to hospitals, AI is changing care.", "I shipped it – then fixed it."]
FIELDS = [  # (question, answer, expect_problem)
    ("Do you have experience with Kubernetes?", "No", True),
    ("Have you used PyTorch in production?", "No", True),
    ("Years of experience with Python", "6", False),
    ("Have you previously worked for Acme?", "No", False),
    ("Will you require visa sponsorship now or in the future?", "No", False),
    ("Are you willing to relocate?", "No", False),
    ("Do you have relatives working here?", "No", False),
    ("Do you have experience with Kubernetes?", "Yes", False),  # style only: a false Yes is the fact-checker's job
    ("Do you have 3+ years of professional industry experience after graduation?", "No", False),
    ("Do you have a PhD or equivalent experience?", "No", False),
    ("Are you comfortable working Eastern Time hours?", "No", False),
    ("Have you worked with our API before?", "No", False),
    ("Do you know anyone who currently works at Acme?", "No", False),
]
TEXT_FIELDS = [  # (question, answer, expect_problem) for free-text answers
    ("Have you previously worked for Acme?", "No, I have not worked at Acme before.", False),
    ("What is your experience with Kubernetes?", "I don't have experience with Kubernetes.", True),
]
for q, a, want in TEXT_FIELDS:
    if bool(style_guard.check_field(q, a, "text")) != want:
        print("TEXT FIELD:", q, "expected problem" if want else "expected clean", style_guard.check_field(q, a, "text"))
        BAD_TEXT_FIELD = True
# an option copied from the form is the employer's text: only the skills rule applies
OPTS = ["Hybrid – 3 days per week in our SF office", "I have read the policy; I agree", "Remote"]

fail = 0
for t in BAD:
    if not style_guard.check_text("t", t):
        print("MISSED:", t[:70])
        fail += 1
for t in GOOD:
    p = style_guard.check_text("t", t)
    if p:
        print("FALSE POSITIVE:", t[:70], p)
        fail += 1
for q, a, want in FIELDS:
    got = bool(style_guard.check_field(q, a, "select"))
    if got != want:
        print("FIELD:", q, a, "expected problem" if want else "expected clean")
        fail += 1
for q, a, want in TEXT_FIELDS:
    fail += bool(style_guard.check_field(q, a, "text")) != want
for o in OPTS[:2]:
    if style_guard.check_field("Which work arrangement do you prefer?", o, "select", options=OPTS):
        print("OPTION FALSE POSITIVE:", o)
        fail += 1
print("failures:", fail)
sys.exit(1 if fail else 0)
