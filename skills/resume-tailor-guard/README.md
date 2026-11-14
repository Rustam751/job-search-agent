# resume-tailor-guard

An Agent Skill that tailors a résumé and a cover letter to one job posting, with a guard against invented facts.

A language model will happily make a résumé sound better than the person is. This skill puts three things in the way:

1. **One rule for the writer.** Every claim must be traceable to a master résumé that the candidate confirmed
   line by line. Every tailored bullet cites the master bullets it came from.
2. **A deterministic guard.** `scripts/resume_guard.py` compares the tailored résumé with the master and rejects
   numbers, listed skills, entry headers (employer, title, dates, link) and contact details that are not there.
3. **Two adversarial reads.** A fact-check that attacks the draft, then a fresh re-check of the final file.

It is a guard, not a guarantee. The script compares numbers and copied strings. It cannot judge what a sentence
means, and it does not read the cover letter. Those depend on the model's fact-check and on the candidate reading
every line before sending it.

Free and open source under the MIT licence.

## What is in the folder

| Path | What it is |
|---|---|
| `SKILL.md` | The instructions an agent follows, in seven steps |
| `scripts/resume_guard.py` | The guard. Also validates a master résumé and writes the Markdown |
| `scripts/check_shape.py` | Shape of the tailored file and length of the cover letter |
| `scripts/style_check.py` | Optional: flags wording that reads as machine-written |
| `references/` | Briefs for the fact-check and the re-check |
| `examples/` | A fictional master résumé, job posting, tailored file and the Markdown it produces |
| `tests/` | Unit tests and a smoke test |

## Use it

You need an agent that can read a `SKILL.md` and run Python 3.8 or newer. The scripts use the standard library only
and make no network calls.

Copy this folder into the place your agent loads skills from. Keep the folder name `resume-tailor-guard`, because
the name in `SKILL.md` has to match it. For Claude Code that place is `~/.claude/skills/` for all your projects, or
`.claude/skills/` inside one project. Other agents that support the Agent Skills format have their own skills
folder, which their documentation names. An agent without skill support can simply be told to read `SKILL.md`.

Then ask, for example:

> Tailor my résumé to this posting: (paste the posting text)

The first run builds your master résumé and asks you to confirm every line of it. Later runs only need a new
posting. The output is a JSON file and two Markdown files, `resume.md` and `cover_letter.md`. There is no Word or
PDF step.

## The scripts on their own

Use `python3` on macOS and Linux, and `python` or `py` on Windows. Each line below runs as written from this folder:

```
python scripts/resume_guard.py examples/master_resume.json
python scripts/resume_guard.py examples/master_resume.json examples/tailored.json
python scripts/check_shape.py examples/tailored.json --final
python scripts/style_check.py examples/tailored.json
```

- The first line validates a master résumé. It exits with 0 when the shape is valid, and its second output line
  says whether the candidate has confirmed the file.
- The second line checks a tailored résumé against the master. Add `--markdown DIR` to write `resume.md` and
  `cover_letter.md` into DIR when the check passes.
- `check_shape.py` checks the shape of the tailored file and the length of the cover letter. `--final` also
  requires a recorded fact-check and a clean re-check. `--resume-only` accepts a file without a cover letter.
  `--letter-words MIN MAX` and `--max-resume-words N` change the length limits.
- `style_check.py` is optional. `--company NAME` exempts an employer name that contains a flagged word.

Exit code 0 means nothing was found, 1 means problems were found, 2 means the check could not run or its output
could not be written. Each script is one self-contained file.

## What the guard reads as a number

- `1,200`, `1 200`, `1'200`, `1200` and `1.2k` are the same number. `1.200` could mean 1.2 or 1,200, so it only
  matches `1.200`.
- A magnitude counts, however it is attached. `$5M`, `5 M`, `5 million`, `5-million`, `5+ million` and `5e6` are
  the same number, and none of them is `5`. The magnitudes it knows are hundred, thousand, lakh, million, crore,
  billion and trillion, also in the plural, and the short forms k, m, mn, mio, mln, mil, MM, b, bn and bln.
- A unit may follow directly: `200ms`, `16GB`, `10x`, `40%`. `x10` is read as 10.
- Number words from two to ninety-nine are read, and so are "a hundred" and "one million". So "three" and "3"
  match, and "twelve engineers" is checked. A bare "one" is not read, because "one of" and "no one" are not
  quantities.
- A magnitude word without a number ("millions of users", "dozens") must stand the same way in the master bullets
  that the bullet cites.
- Digits glued to the end of a name are not numbers: `S3`, `p95`, `v2.1`.

A false alarm is the safe direction. If the guard flags a number that is true, write it the way the master does.

## What it does not catch

- A target stated as a result, a dropped qualifier such as "pilot", a design described as shipped, a wider scope.
- A changed unit, or a number moved to a different thing. 20 minutes and 20 seconds, or $50 and 50%, are the same
  number to the script.
- A quantity without a number: "doubled", "twice", "half", "zero", "several", "tenfold". Number words above
  ninety-nine are read piece by piece ("one hundred and twenty" is read as 100 and 20), and number words in other
  languages are not read.
- A tool, a job title or any other claim written in words inside a bullet or the summary.
- What a section title or a skills label implies. The script only checks that their words come from the master's
  own section and group names or from a short list of plain words.
- A bullet that cites a master bullet which does not support it, or an id added only to make a number pass.
- Anything in the cover letter.
- A mistake in the master résumé itself. The guard trusts the master, which is why the candidate confirms it.

## Tests

```
python -m unittest discover -s tests
python tests/smoke_test.py
```

The smoke test runs the scripts on the example. The guard must accept it and must reject an invented number, an
invented skill, a changed date and a number with a magnitude glued on.

## Licence

MIT. See `LICENSE`.

Everyone and everything in `examples/` and `tests/` is invented. Any resemblance to a real person, company or
school is a coincidence.
