"""Certificate numbers, QR codes and the PDF certificate.

A certificate number looks like  UI/CLR/2026/000123-9F3A7C21  where the last part is a keyed
HMAC-SHA256 of the request id, matric number and year. It cannot be guessed or edited, and the
public verification page also checks it against the database.
"""
import hashlib
import hmac
import io
import re
from urllib.parse import quote

from flask import current_app
from reportlab.graphics.barcode import qrencoder
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from .db import now, query

_NUMBER_RE = re.compile(r"^UI/CLR/(\d{4})/(\d{6})-([0-9A-F]{8})$")


def _sig(request_id: int, matric_no: str, year: str) -> str:
    key = current_app.config["CERT_KEY"].encode()
    msg = ("%d|%s|%s" % (request_id, matric_no, year)).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:8].upper()


def issue(request_id: int, matric_no: str) -> str:
    year = now()[:4]
    return "UI/CLR/%s/%06d-%s" % (year, request_id, _sig(request_id, matric_no, year))


def verify(number: str):
    """Return a dict describing the certificate if it is genuine, otherwise None."""
    m = _NUMBER_RE.match((number or "").strip().upper())
    if not m:
        return None
    year, rid, sig = m.group(1), int(m.group(2)), m.group(3)
    row = query(
        "SELECT r.*, s.faculty, s.department, s.session, u.full_name FROM clearance_requests r "
        "JOIN students s ON s.matric_no = r.matric_no JOIN users u ON u.user_id = s.user_id "
        "WHERE r.request_id = ? AND r.overall_status = 'cleared'", (rid,), one=True)
    if row is None or row["certificate_no"] != number.strip().upper():
        return None
    if not hmac.compare_digest(sig, _sig(rid, row["matric_no"], year)):
        return None
    return dict(row)


def verify_url(number: str) -> str:
    return "%s/verify?no=%s" % (current_app.config["BASE_URL"].rstrip("/"), quote(number, safe=""))


def qr_matrix(text: str):
    qr = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    qr.addData(text)
    qr.make()
    n = qr.getModuleCount()
    return [[bool(qr.isDark(r, c)) for c in range(n)] for r in range(n)]


def qr_svg(text: str, px: int = 4) -> str:
    """Inline SVG of the QR code (4-module quiet zone included)."""
    m = qr_matrix(text)
    n = len(m)
    size = (n + 8) * px
    rects = []
    for r, row in enumerate(m):
        c = 0
        while c < n:           # merge horizontal runs to keep the markup small
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append('<rect x="%d" y="%d" width="%d" height="%d"/>' % ((start + 4) * px, (r + 4) * px, (c - start) * px, px))
            else:
                c += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
            'aria-label="QR code that opens the certificate verification page">'
            '<rect width="100%%" height="100%%" fill="#fff"/><g fill="#000">%s</g></svg>') % (size, size, size, size, "".join(rects))


def certificate_pdf(req) -> bytes:
    """`req` is the dict returned by verify() (so only genuine, cleared requests get a PDF)."""
    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle("Graduation Clearance Certificate - " + req["matric_no"])
    navy = colors.HexColor("#1F3864")
    c.setStrokeColor(navy)
    c.setLineWidth(4)
    c.rect(24, 24, w - 48, h - 48)
    c.setLineWidth(1)
    c.rect(34, 34, w - 68, h - 68)
    c.setFillColor(navy)
    c.setFont("Times-Bold", 20)
    c.drawCentredString(w / 2, h - 90, "UNIVERSITY OF IBADAN")
    c.setFont("Times-Roman", 13)
    c.drawCentredString(w / 2, h - 112, "Ibadan, Oyo State, Nigeria")
    c.setFont("Times-Bold", 30)
    c.drawCentredString(w / 2, h - 170, "GRADUATION CLEARANCE CERTIFICATE")
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 15)
    c.drawCentredString(w / 2, h - 220, "This is to certify that")
    c.setFont("Times-Bold", 26)
    c.drawCentredString(w / 2, h - 258, req["full_name"].upper())
    c.setFont("Times-Roman", 14)
    c.drawCentredString(w / 2, h - 288, "Matriculation No. %s  |  Department of %s, %s" % (req["matric_no"], req["department"], req["faculty"]))
    n_units = query("SELECT COUNT(*) AS n FROM clearance_steps WHERE request_id = ? AND status = 'approved'",
                    (req["request_id"],), one=True)["n"]
    c.drawCentredString(w / 2, h - 322, "has been cleared by all %d clearance units and is eligible for inclusion" % n_units)
    c.drawCentredString(w / 2, h - 342, "in the graduation list for the %s session." % req["session"])
    # QR code (drawn as vector squares)
    m = qr_matrix(verify_url(req["certificate_no"]))
    cell, qx, qy = 3.2, 60, 60
    c.setFillColor(colors.black)
    for r, row in enumerate(m):
        for col, dark in enumerate(row):
            if dark:
                c.rect(qx + col * cell, qy + (len(m) - 1 - r) * cell, cell, cell, stroke=0, fill=1)
    c.setFont("Times-Roman", 11)
    c.drawString(qx + len(m) * cell + 18, qy + 52, "Certificate No: " + req["certificate_no"])
    c.drawString(qx + len(m) * cell + 18, qy + 36, "Issued: " + (req["cleared_at"] or "")[:10])
    c.drawString(qx + len(m) * cell + 18, qy + 20, "Scan the QR code or visit /verify to confirm authenticity.")
    c.showPage()
    c.save()
    return buf.getvalue()
