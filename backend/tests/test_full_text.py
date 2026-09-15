import io
import tarfile

import pytest

from app.services import pdf


def test_tex_source_selects_main_file_and_inlines_sections():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        files = {
            "main.tex": r"""\documentclass{article}
\begin{document}
\section{Introduction}
This is a sufficiently long research paper introduction. """ + ("evidence " * 80) + r"""
\input{sections/method}
\end{document}
""",
            "sections/method.tex": r"""\section{Method}
The proposed method combines planning and representation learning.
""",
            "appendix.tex": r"""\section{Appendix} supplemental material""",
        }
        for name, content in files.items():
            encoded = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            archive.addfile(info, io.BytesIO(encoded))

    text = pdf._extract_tex_text_sync(buffer.getvalue(), 20_000)

    assert "Introduction" in text
    assert "proposed method" in text


@pytest.mark.asyncio
async def test_full_text_falls_back_from_tex_to_html(monkeypatch):
    async def failed_tex(*_args):
        raise ValueError("source unavailable")

    async def good_html(*_args):
        return "readable html " * 100

    async def unexpected_pdf(*_args):
        raise AssertionError("PDF should not be used after HTML succeeds")

    monkeypatch.setattr(pdf, "extract_tex_text", failed_tex)
    monkeypatch.setattr(pdf, "extract_html_text", good_html)
    monkeypatch.setattr(pdf, "extract_pdf_text", unexpected_pdf)

    text, mode, errors = await pdf.extract_arxiv_text(
        "2609.12036",
        1,
        "https://arxiv.org/pdf/2609.12036",
    )

    assert mode == "html"
    assert text.startswith("readable html")
    assert errors and errors[0].startswith("tex:")
