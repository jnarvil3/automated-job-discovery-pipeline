"""
Shared cover letter generation for all apply methods.
Generates text, PDF, and DOCX versions for ATS uploads.
"""

import os
from datetime import date
from pathlib import Path

from openai import OpenAI
from core.models import Job


LETTERS_DIR = Path(__file__).parent.parent / "data" / "cover_letters"

LETTER_PROMPT = """You write professional cover letters for a specific candidate. Return ONLY the letter body — no date, no address block, no greeting, no signature (those are added separately by the system).

CANDIDATE — Amane Aguiar Dias de Azevedo:
- MA in International and Development Economics at HTW Berlin (graduating Dec 2026, currently on thesis)
- BA in Economics from Federal University of Bahia
- 8+ years of professional experience in finance, impact investing, consulting, and program management
- Languages: English (C2), Portuguese (native), Spanish (C2), German (A1)
- Currently in Berlin, available immediately

KEY EXPERIENCE TO DRAW FROM (select what's relevant to each job):
1. Sitawi (Impact Investing) — Consultant, then Senior Coordinator, then Senior Analyst:
   - Due diligence including financial modeling (P&L, Balance Sheet, Cash Flow analyses)
   - Portfolio monitoring of impact-oriented businesses, technical assistance
   - Reporting to global partners (USAID, GIZ), investor relations (UHNW individuals)
   - Led development of a financial instrument for climate justice organizations
   - Feasibility analysis of a pioneer impact-linked investment fund
2. AVSI Brasil — Program Manager:
   - Managed multi-million-dollar budgets (3M USD) for UNHCR/UNICEF projects at Brazil-Venezuela border
   - Led teams of 180+ staff, budget negotiations with UN agencies
   - Financial and narrative reporting to donor standards, compliance with external audits (BDO)
3. AVSI Foundation — Regional Development Officer:
   - Fundraising for Italian foundation HQ in Latin America
   - Wrote and submitted 10+ approved proposals to USAID, EU, and UN
   - Prepared annual Social Balance report at international level
4. ERB Renewable Energies / Bahiagás — Intern:
   - FP&A and Controlling support: SAP data entry, Excel reports, budget management, financial closing

STYLE (follow the "Flink letter" approach):
- Opening: Express excitement about the specific role and company. State your 8+ years of experience and current studies in one sentence.
- Body: 2-3 paragraphs, each focusing on the most relevant experience for THIS role. Describe what you actually did — concrete responsibilities and skills, not vague claims. Connect your experience to what the company needs.
- Closing: Mention you're in Berlin, finishing your Master's thesis, available for the position. Mention English, Portuguese, Spanish fluency. Express eagerness to contribute.

RULES:
- Keep it 250-350 words — substantive but not excessive
- Be warm, confident, and specific — not generic
- Reference the specific company and role throughout
- Select only the experience most relevant to the job description
- Do NOT mention German skills
- Write in proper paragraphs (not bullet points)
- Do NOT fabricate experience — only use what is listed above
"""


def generate_cover_letter(job: Job, client: OpenAI | None = None) -> str:
    """Generate a short, tailored cover letter for the job. Returns plain text."""
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return ""
        client = OpenAI(api_key=api_key, timeout=30)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=700,
            messages=[
                {"role": "system", "content": LETTER_PROMPT},
                {"role": "user", "content": (
                    f"Write a cover letter body for:\n"
                    f"Role: {job.title}\n"
                    f"Company: {job.company}\n"
                    f"Location: {job.location}\n"
                    f"Description: {job.description[:1500]}"
                )},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [cover_letter] Failed to generate for {job.title}: {e}")
        return ""


def _format_full_letter(body: str, job: Job) -> str:
    """Wrap the GPT-generated body with proper letter formatting."""
    today = date.today().strftime("%B %d, %Y")
    return (
        f"{today}\n\n"
        f"Re: {job.title}\n"
        f"{job.company}\n\n"
        f"Dear Hiring Team,\n\n"
        f"{body}\n\n"
        f"Best regards,\n"
        f"Amane Aguiar Dias de Azevedo"
    )


def generate_cover_letter_pdf(job: Job, letter_text: str) -> str:
    """
    Generate a PDF cover letter. Returns the file path.
    Uses reportlab if available, falls back to FPDF.
    """
    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    safe_company = "".join(c for c in job.company if c.isalnum() or c in " -_")[:30].strip()
    filename = f"CoverLetter_{safe_company}_{job.id}.pdf"
    filepath = LETTERS_DIR / filename
    full_text = _format_full_letter(letter_text, job)

    # Try reportlab first (better quality)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT

        doc = SimpleDocTemplate(str(filepath), pagesize=A4,
                                leftMargin=2.5*cm, rightMargin=2.5*cm,
                                topMargin=2.5*cm, bottomMargin=2.5*cm)
        styles = getSampleStyleSheet()
        body_style = ParagraphStyle('Body', parent=styles['Normal'],
                                     fontSize=11, leading=15, alignment=TA_LEFT,
                                     spaceAfter=10)

        story = []
        for paragraph in full_text.split("\n\n"):
            paragraph = paragraph.replace("\n", "<br/>")
            story.append(Paragraph(paragraph, body_style))
            story.append(Spacer(1, 6))

        doc.build(story)
        return str(filepath)

    except ImportError:
        pass

    # Fallback: fpdf2
    try:
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=25)
        pdf.set_margins(25, 25, 25)
        pdf.set_font("Helvetica", size=11)

        for paragraph in full_text.split("\n\n"):
            pdf.multi_cell(0, 6, paragraph)
            pdf.ln(4)

        pdf.output(str(filepath))
        return str(filepath)

    except ImportError:
        pass

    # Last resort: minimal PDF by hand (no dependencies)
    _write_minimal_pdf(filepath, full_text)
    return str(filepath)


def generate_cover_letter_docx(job: Job, letter_text: str) -> str:
    """
    Generate a DOCX cover letter. Returns the file path.
    Requires python-docx.
    """
    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    safe_company = "".join(c for c in job.company if c.isalnum() or c in " -_")[:30].strip()
    filename = f"CoverLetter_{safe_company}_{job.id}.docx"
    filepath = LETTERS_DIR / filename
    full_text = _format_full_letter(letter_text, job)

    try:
        from docx import Document
        from docx.shared import Pt

        doc = Document()
        style = doc.styles['Normal']
        style.font.name = 'Calibri'
        style.font.size = Pt(11)

        for paragraph in full_text.split("\n\n"):
            doc.add_paragraph(paragraph)

        doc.save(str(filepath))
        return str(filepath)

    except ImportError:
        print("  [cover_letter] python-docx not installed — skipping DOCX generation")
        return ""


def _write_minimal_pdf(filepath: Path, text: str):
    """Write a bare-minimum valid PDF with no external dependencies."""
    lines = text.split("\n")
    # Build PDF content stream
    content_lines = []
    y = 750  # start near top of page
    for line in lines:
        if not line.strip():
            y -= 14
            continue
        # Escape special PDF characters
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"BT /F1 11 Tf 72 {y} Td ({safe}) Tj ET")
        y -= 14
        if y < 72:  # don't write below margin
            break

    stream = "\n".join(content_lines)

    objects = [
        # 1: Catalog
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj",
        # 2: Pages
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj",
        # 3: Page
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj",
        # 4: Content stream
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n{stream}\nendstream\nendobj",
        # 5: Font
        "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj",
    ]

    with open(filepath, "wb") as f:
        f.write(b"%PDF-1.4\n")
        offsets = []
        for obj in objects:
            offsets.append(f.tell())
            f.write(obj.encode("latin-1", errors="replace") + b"\n")
        xref_pos = f.tell()
        f.write(b"xref\n")
        f.write(f"0 {len(objects) + 1}\n".encode())
        f.write(b"0000000000 65535 f \n")
        for off in offsets:
            f.write(f"{off:010d} 00000 n \n".encode())
        f.write(b"trailer\n")
        f.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
        f.write(b"startxref\n")
        f.write(f"{xref_pos}\n".encode())
        f.write(b"%%EOF\n")
