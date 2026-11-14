"""Render a tailored résumé JSON into a one-style .docx and .pdf (PDF export uses Microsoft Word).

Usage: python render_resume.py tailored.json out/path/basename [--png]

Tailored JSON shape:
{
  "contact": {"display_name", "location", "phone", "email", "website", "github", "linkedin"},
  "summary": "text with optional **bold** spans",
  "skills": [{"label": "LLM & GenAI", "items": ["RAG", ...]}],
  "sections": [
    {"title": "Experience", "entries": [
      {"heading": "Acme Robotics", "heading_link": "", "heading_right": "Austin, TX (Remote)",
       "subheading": "ML Engineer", "subheading_right": "Jan 2021 – Dec 2022",
       "bullets": [{"text": "...", "source_ids": ["ex1"]}]}
    ]}
  ]
}
"""
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

NAVY = RGBColor(0x1F, 0x38, 0x64)
GRAY = RGBColor(0x59, 0x59, 0x59)
FONT = "Times New Roman"
BODY_PT = 10.5
MARGIN_IN = 0.6
PAGE_W_IN = 8.5
TEXT_W_IN = PAGE_W_IN - 2 * MARGIN_IN


def set_font(run, size=None, bold=None, italic=None, color=None):
    size = size or BODY_PT
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def tight(p, before=0, after=0, keep_next=False):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = 1.0
    pf.keep_together = True
    pf.keep_with_next = keep_next


def add_marked_text(p, text, size=None, italic=None, color=None):
    """Add text where **spans** are bold."""
    size = size or BODY_PT
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            set_font(p.add_run(part), size=size, bold=(i % 2 == 1), italic=italic, color=color)


def add_hyperlink(p, url, text, size, color):
    part = p.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), FONT)
    rpr.append(fonts)
    col = OxmlElement("w:color")
    col.set(qn("w:val"), str(color))
    rpr.append(col)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size * 2)))
    rpr.append(sz)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    p._p.append(link)


def bottom_border(p, color="1F3864"):
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "8"), ("w:space", "1"), ("w:color", color)):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    ppr.append(bdr)


def right_tab(p):
    p.paragraph_format.tab_stops.add_tab_stop(Inches(TEXT_W_IN), WD_TAB_ALIGNMENT.RIGHT)


def section_heading(doc, title):
    p = doc.add_paragraph()
    tight(p, before=6, after=2, keep_next=True)
    set_font(p.add_run(title.upper()), size=BODY_PT + 1, bold=True, color=NAVY)
    bottom_border(p)


def entry(doc, e):
    p = doc.add_paragraph()
    tight(p, before=3, keep_next=True)
    right_tab(p)
    if e.get("heading_link"):
        set_font(p.add_run(e["heading"]), bold=True)
        set_font(p.add_run(" — "), bold=False)
        add_hyperlink(p, e["heading_link"], e.get("link_label", "GitHub"), BODY_PT, "1F3864")
    else:
        add_marked_text(p, f"**{e['heading']}**")
    if e.get("heading_right"):
        set_font(p.add_run("\t" + e["heading_right"]), color=GRAY)
    if e.get("subheading") or e.get("subheading_right"):
        p2 = doc.add_paragraph()
        tight(p2, keep_next=True)
        right_tab(p2)
        set_font(p2.add_run(e.get("subheading", "")), italic=True, color=GRAY)
        if e.get("subheading_right"):
            set_font(p2.add_run("\t" + e["subheading_right"]), italic=True, color=GRAY)
    for b in e.get("bullets", []):
        bp = doc.add_paragraph(style="List Bullet")
        tight(bp, after=1)
        bp.paragraph_format.left_indent = Inches(0.22)
        bp.paragraph_format.first_line_indent = Inches(-0.16)
        add_marked_text(bp, b["text"] if isinstance(b, dict) else b)


def build(data, docx_path, body_pt=None, margin_in=None):
    """Render the résumé. body_pt/margin_in override the defaults for one-page auto-fit."""
    global BODY_PT, MARGIN_IN, TEXT_W_IN
    BODY_PT = body_pt or 10.5
    MARGIN_IN = margin_in or 0.6
    TEXT_W_IN = PAGE_W_IN - 2 * MARGIN_IN

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(PAGE_W_IN), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Inches(MARGIN_IN))
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY_PT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    c = data["contact"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p)
    set_font(p.add_run(c["display_name"]), size=20, bold=True, color=NAVY)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tight(p, after=2)
    set_font(p.add_run(f"{c['location']} | {c['phone']} | {c['email']} | "), size=10, color=GRAY)
    links = [("Website", c.get("website")), ("GitHub", c.get("github")), ("LinkedIn", c.get("linkedin"))]
    links = [(label, url) for label, url in links if url]
    for i, (label, url) in enumerate(links):
        add_hyperlink(p, url, label, 10, "1F3864")
        if i < len(links) - 1:
            set_font(p.add_run(" | "), size=10, color=GRAY)

    if data.get("summary"):
        section_heading(doc, "Summary")
        p = doc.add_paragraph()
        tight(p, after=1)
        add_marked_text(p, data["summary"])

    if data.get("skills"):
        section_heading(doc, "Skills")
        for s in data["skills"]:
            p = doc.add_paragraph()
            tight(p)
            set_font(p.add_run(f"{s['label']}: "), bold=True)
            set_font(p.add_run(", ".join(s["items"])))

    for s in data["sections"]:
        section_heading(doc, s["title"])
        for e in s["entries"]:
            entry(doc, e)

    doc.core_properties.author = c["display_name"]
    doc.core_properties.title = f"{c['display_name']} — Résumé"
    doc.save(docx_path)


def to_pdf(docx_path, pdf_path):
    """One-off conversion for the standalone CLIs (build_all.py keeps one Word session for a whole batch)."""
    from word_convert import WordSession
    with WordSession() as w:
        w.convert(docx_path, pdf_path)


def inspect_pdf(pdf_path, png=False):
    import pymupdf
    doc = pymupdf.open(pdf_path)
    words = sum(len(page.get_text().split()) for page in doc)
    pngs = []
    if png:
        for i, page in enumerate(doc):
            out = Path(pdf_path).with_name(f"{Path(pdf_path).stem}_p{i + 1}.png")
            page.get_pixmap(dpi=110).save(out)
            pngs.append(str(out))
    return {"pages": doc.page_count, "words": words, "pngs": pngs}


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
