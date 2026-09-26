"""One Word session for many .docx → .pdf conversions.

docx2pdf starts a fresh Word instance per call, which is slow and deadlocks when two builds overlap.
WordSession keeps a single instance open for the whole batch.

    with WordSession() as w:
        w.convert("a.docx", "a.pdf")
"""
import os

WD_FORMAT_PDF = 17


class WordSession:
    def __init__(self):
        self.word = None

    def __enter__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self.word = win32com.client.DispatchEx("Word.Application")
        self.word.Visible = False
        self.word.DisplayAlerts = 0
        return self

    def convert(self, docx_path, pdf_path):
        docx_path, pdf_path = os.path.abspath(docx_path), os.path.abspath(pdf_path)
        doc = self.word.Documents.Open(docx_path, ReadOnly=1, AddToRecentFiles=False, Visible=False)
        try:
            doc.SaveAs(pdf_path, FileFormat=WD_FORMAT_PDF)
        finally:
            doc.Close(0)
        return pdf_path

    def __exit__(self, *exc):
        import pythoncom
        try:
            if self.word is not None:
                self.word.Quit(0)
        finally:
            self.word = None
            pythoncom.CoUninitialize()
        return False
