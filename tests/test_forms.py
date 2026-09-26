"""Regression cases for forms.flags / auth_category / auth_answer_problem. Run: python tests/test_forms.py

Runs with an empty work-authorization policy (the public default), so every such question goes to the human. The
candidate's own policy lives in private/config.local.json and is not part of these tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forms
forms.AUTH_POLICY = {}  # public default: nothing about work authorization is answered automatically
def Q(label, t="select", req=True, opts=(), desc=""): return dict(label=label, type=t, required=req, options=list(opts), description=desc)
YN=("Yes","No")
ask_cases = [  # (question, expected: 'ask' | 'none' | 'stop')
 (Q("Do you consent to a background investigation and drug test in conjunction with receiving an offer?",opts=YN),"ask"),
 (Q("What is your GPA? (as shown on your official transcript)","text"),"none"),
 (Q("Cover letter (feel free to reference specific projects)","long_text"),"none"),
 (Q("How did you hear about us? (referral, reference from a friend)"),"none"),
 (Q("Will you now or in the future require Acme to commence (sponsor) an immigration case?",opts=YN),"ask"),
 (Q("Undergraduate Transcript","file"),"none"), (Q("Graduate Transcript","file"),"ask"), (Q("Resume/CV","file"),"none"),
 (Q("Upload a copy of your EAD card","file"),"ask"), (Q("Letter of recommendation","file"),"ask"),
 (Q("Citizenship Status",opts=("U.S. Citizen","U.S. Permanent Resident (Green Card)","Refugee","Asylee","Other / Eligible for required authorization")),"ask"),
 (Q("Please list all countries of which you are a citizen","long_text"),"ask"), (Q("Where were you born?","text"),"ask"),
 (Q("Do you have any relatives employed by the company?",opts=YN),"ask"), (Q("Rate your Python proficiency relative to your peers"),"none"),
 (Q("Are you bound by a non-compete?",opts=YN),"ask"), (Q("Are you legally authorized to work in the US?",opts=YN),"ask"),
 (Q("Please list three professional references","long_text"),"ask"), (Q("Reference 1 Name","text"),"ask"),
 (Q("Do you consent to AI note-taking during your video interview?",opts=YN),"none"), (Q("AI Interview Consent",opts=YN),"none"),
 (Q("Upload a 2-minute video about yourself","file"),"stop"), (Q("Link to a short video (Loom or YouTube) introducing yourself","text"),"stop"),
 (Q("Tell us about a one-way door decision you made","long_text"),"none"), (Q("Describe a project where you recorded a video dataset","long_text"),"none"),
 (Q("Would you like to opt in to text message updates?",opts=YN),"none"), (Q("Are you currently on OPT?",opts=YN),"ask"),
 (Q("What was your most recent base salary?","text"),"ask"), (Q("What are your salary expectations?","text"),"none"),
 (Q("Are you a US Citizen or Permanent Resident?",opts=YN),"ask"), (Q("Are you a U.S. citizen, green card holder, or otherwise authorized to work?",opts=YN),"ask"),
 (Q("Please select your employment eligibility",opts=("U.S. Citizen","Permanent Resident","Visa holder")),"ask"),
 (Q("Have you ever been incarcerated?",opts=YN),"ask"), (Q("Have you ever been sentenced to jail or prison?",opts=YN),"ask"),
]
bad=0
for q,want in ask_cases:
    f=forms.flags([q]); got='stop' if f['disqualify'] else ('ask' if f['ask_russ'] else 'none')
    if got!=want: bad+=1; print('BAD ', want,'got',got,'|',q['label'][:80])
# classification only: which category each work-authorization question falls into
categories=[(Q("Are you legally authorized to work in the United States without the need for employer sponsorship?",opts=YN),"authorized"),
 (Q("Will you require visa sponsorship now or in the near future?",opts=YN),"sponsorship"),
 (Q("Do you require sponsorship?",opts=("I do not require sponsorship","I will require sponsorship")),"sponsorship"),
 (Q("Are you authorized to work in the U.S.?",opts=YN),"authorized"),
 (Q("Do you hold a green card?",opts=YN),"status_yes_no"), (Q("Are you a U.S. citizen?",opts=YN),"status_yes_no"),
 (Q("Are you currently on OPT?",opts=YN),"visa_type"),
 (Q("Citizenship Status",opts=("U.S. Citizen","Other / Eligible for required authorization")),"status_select"),
 (Q("What is your current work authorization status?","text"),"status_select"),
 (Q("Please select your employment eligibility",opts=("U.S. Citizen","Permanent Resident","Visa holder")),"no_valid_option"),
 (Q("Are you a U.S. citizen, green card holder, or otherwise authorized to work?",opts=YN),"mixed"),
 (Q("What is your GPA?","text"),None)]
for q,want in categories:
    got=forms.auth_category(q)
    if got!=want: bad+=1; print('BAD category', q['label'][:60], got, 'want', want)
# the answer checker's contract, for each kind of configured answer (not anyone's actual answers)
contract=[("yes","Yes",False),("yes","Yes, as a permanent resident",True),("no","No",False),
 ("no","No, I am a green card holder",True),("no","Yes",True),("no","I do not require sponsorship",False),
 ("other","Other / Eligible for required authorization",False),("other","Other: Permanent Resident",True),
 ("other",forms.OTHER_STATUS,False)]
for kind,ans,want in contract:
    if bool(forms.auth_answer_problem(kind,ans))!=want: bad+=1; print('BAD contract', kind, ans)
st=[{"label":"Do you consent to a background investigation and drug test in conjunction with receiving an offer?","match":"background (check|investigation)|drug (test|screen)","answer":"Yes"}]
for lab,want in [("Do you consent to a background investigation and drug test in conjunction with receiving an offer?",True),("Have you ever failed a pre-employment drug test?",False),("Has a background check ever revealed a criminal conviction?",False),("Are you willing to undergo a background check?",True)]:
    got=bool(forms.flags([Q(lab,opts=YN)],st)['standing'])
    if got!=want: bad+=1; print('BAD standing',lab,got)
if forms.same_question("What is your current salary?","What is your desired salary?"): bad+=1; print("BAD same_question: current vs desired salary")
if not forms.same_question("Location","Location (City)") and forms.same_question("Location","Location City"): bad+=1; print("BAD same_question: Location")
print('failures:',bad)
sys.exit(1 if bad else 0)
