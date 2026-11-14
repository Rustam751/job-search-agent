"""Render a cover letter JSON into .docx and .pdf with the same header style as the résumé.

Usage: python render_cover_letter.py letter.json out/path/basename

Letter JSON: {"contact": {...same as résumé...}, "date": "September 22, 2026", "company": "Acme",
              "role": "Machine Learning Engineer", "greeting": "Dear Acme Hiring Team,",
              "paragraphs": ["...", "..."], "closing": "Sincerely,"}
"""
import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from render_resume import FONT, GRAY, NAVY, add_hyperlink, add_marked_text, bottom_border, inspect_pdf, set_font, tight, to_pdf

BODY = 11


def build(data, docx_path):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Inches(0.9))
    for side in ("top_margin", "bottom_margin"):
        setattr(sec, side, Inches(0.7))
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    c = data["contact"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p)
    set_font(p.add_run(c["display_name"]), size=20, bold=True, color=NAVY)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p, after=4)
    set_font(p.add_run(f"{c['location']} | {c['phone']} | {c['email']} | "), size=10, color=GRAY)
    links = [(k, c.get(v)) for k, v in (("Website", "website"), ("GitHub", "github"), ("LinkedIn", "linkedin")) if c.get(v)]
    for i, (label, url) in enumerate(links):
        add_hyperlink(p, url, label, 10, "1F3864")
        if i < len(links) - 1:
            set_font(p.add_run(" | "), size=10, color=GRAY)
    bottom_border(p)

    def para(text="", before=0, after=8, bold=False):
        q = doc.add_paragraph()
        tight(q, before=before, after=after)
        q.paragraph_format.line_spacing = 1.08
        if bold:
            set_font(q.add_run(text), size=BODY, bold=True)
        else:
            add_marked_text(q, text, size=BODY)
        return q

    para(data["date"], before=10, after=10)
    para(f"{data['company']} — {data['role']}", after=10, bold=True)
    para(data["greeting"], after=8)
    for text in data["paragraphs"]:
        para(text, after=8)
    para(data.get("closing", "Sincerely,"), before=4, after=2)
    para(c["display_name"], after=0)

    doc.core_properties.author = c["display_name"]
    doc.core_properties.title = f"{c['display_name']} — Cover Letter — {data['company']}"
    doc.save(docx_path)


def main():
    src, base = Path(sys.argv[1]), Path(sys.argv[2])
    base.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(src.read_text(encoding="utf-8"))
    docx_path, pdf_path = base.with_suffix(".docx"), base.with_suffix(".pdf")
    build(data, docx_path)
    to_pdf(docx_path, pdf_path)
    print(json.dumps({"docx": str(docx_path), "pdf": str(pdf_path), **inspect_pdf(pdf_path, "--png" in sys.argv)}))


if __name__ == "__main__":
    main()
