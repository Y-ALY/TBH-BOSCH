import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def create_hackathon_test_pdf(filename="hackathon_test_document.pdf"):
    # Target directory check
    print(f"Creating PDF: {os.path.abspath(filename)}")
    
    # Set up document
    doc = SimpleDocTemplate(filename, pagesize=letter,
                            rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
    story = []
    
    # Custom Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=12
    )
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2C5282"),
        spaceBefore=18,
        spaceAfter=8
    )
    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        spaceAfter=10
    )
    
    # --- DOCUMENT HEADER ---
    story.append(Paragraph("TUM TechOn 2026 — Internal Audit Logs", title_style))
    story.append(Paragraph("<b>Document Reference:</b> REQ-2026-SYS992<br/><b>Classification:</b> Confidential / Internal Only", body_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A365D"), spaceAfter=15))
    
    # --- SECTION 1 ---
    story.append(Paragraph("1. Reimbursement Operations & Financial Claims", section_style))
    text1 = """
    The accounting unit processed the following transactional onboarding expenses for the Heilbronn campus operations. 
    Please verify that processing parameters line up with corporate data policies before cold archiving.
    """
    story.append(Paragraph(text1, body_style))
    
    pii_text1 = """
    <b>Claimant:</b> Jonas Weber<br/>
    <b>Department:</b> Information Engineering<br/>
    <b>Direct Line:</b> +49 (0) 89 289 01<br/>
    <b>Primary Wire Destination (IBAN):</b> DE89370400440532013000<br/>
    <b>Routing Code (BIC):</b> WELADED1MUC
    """
    story.append(Paragraph(pii_text1, body_style))
    story.append(Spacer(1, 10))
    
    # --- SECTION 2 ---
    story.append(Paragraph("2. Vendor & Subcontractor External Clearances", section_style))
    text2 = """
    External technical deployment audits for system engineering cohorts require active review vectors. 
    The external architecture representative can be engaged via the credentials below.
    """
    story.append(Paragraph(text2, body_style))
    
    pii_text2 = """
    <b>Contractor Entity:</b> Sarah Jenkins (Cloud Architecture Solutions)<br/>
    <b>Secure Correspondence:</b> s.jenkins@cloud-arch.com<br/>
    <b>Mobile Routing Endpoint:</b> +49 151 23456789<br/>
    <b>Disbursement Vault Account (IBAN):</b> DE21500300000123456789
    """
    story.append(Paragraph(pii_text2, body_style))
    
    # Force a page break to test multi-page text ingestion
    from reportlab.platypus import PageBreak
    story.append(PageBreak())
    
    # --- PAGE 2 ---
    story.append(Paragraph("3. Identity & Access Management (IAM) Provisioning", section_style))
    text3 = """
    The following staging directory logs contain provisioned corporate assets flagged for human compliance sign-off 
    by the designated Master of Data.
    """
    story.append(Paragraph(text3, body_style))
    
    pii_text3 = """
    <b>Target User ID:</b> Michael Chen<br/>
    <b>Academic Core Directory:</b> m.chen@student.tum.de<br/>
    <b>Alternative Fallback Mailbox:</b> mike.chen.private@gmail.com<br/>
    <b>System Clearance Token:</b> Tier-3 DevOps Cluster Authorization
    """
    story.append(Paragraph(pii_text3, body_style))
    
    # Build Document
    doc.build(story)
    print("✅ Test PDF generated successfully!")

if __name__ == "__main__":
    create_hackathon_test_pdf()