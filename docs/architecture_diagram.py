"""Architecture diagram of the CI triage agent, drawn from code (reportlab).

Usage:  python docs/architecture_diagram.py
Writes docs/architecture-diagram.pdf (vector) and, if pypdfium2 is installed, docs/architecture-diagram.png.
Needs only `pip install reportlab`. The assessment document imports `architecture_diagram()` from this file,
so what the document shows is exactly what this code draws.
"""
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing, Group, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.units import mm

WIDTH = 170 * mm
ACCENT = colors.HexColor("#1F3A5F")
GREY = colors.HexColor("#5B6573")


def architecture_diagram():
    W, H = WIDTH, 292
    d = Drawing(W, H)
    ink, ARROW = colors.HexColor("#1F2A37"), colors.HexColor("#33415C")
    people_f, people_l = colors.HexColor("#E8F4EC"), colors.HexColor("#5E9E73")
    gh_f, gh_l = colors.HexColor("#F3F5F8"), colors.HexColor("#9AA5B4")
    tri_f, tri_l = colors.HexColor("#FFF7E6"), colors.HexColor("#D9A441")
    ant_f, ant_l = colors.HexColor("#FBEDE6"), colors.HexColor("#C46A45")
    slk_f, slk_l = colors.HexColor("#F1EAF6"), colors.HexColor("#7B4F9A")

    def box(x, y, w, h, head, lines, fill=colors.white, line=colors.HexColor("#8C99AA"), dashed=False):
        r = Rect(x, y, w, h, rx=5, ry=5, fillColor=fill, strokeColor=line, strokeWidth=1)
        if dashed:
            r.strokeDashArray = [3, 2]
        d.add(r)
        d.add(String(x + 6, y + h - 12, head, fontName="Helvetica-Bold", fontSize=8, fillColor=ink))
        for i, text in enumerate(lines):
            d.add(String(x + 6, y + h - 23 - i * 8.6, text, fontName="Helvetica", fontSize=6.8, fillColor=ink))

    def arrow(points, dashed=False):
        for a, b in zip(points, points[1:]):
            ln = Line(a[0], a[1], b[0], b[1], strokeColor=ARROW, strokeWidth=1.2)
            if dashed:
                ln.strokeDashArray = [3, 2]
            d.add(ln)
        (x0, y0), (x1, y1) = points[-2], points[-1]
        dx, dy = x1 - x0, y1 - y0
        n = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / n, dy / n
        s = 5.5
        d.add(Polygon([x1, y1, x1 - s * ux + 0.38 * s * -uy, y1 - s * uy + 0.38 * s * ux,
                       x1 - s * ux - 0.38 * s * -uy, y1 - s * uy - 0.38 * s * ux], fillColor=ARROW, strokeColor=ARROW))

    def note(x, y, text, anchor="start"):
        d.add(String(x, y, text, fontName="Helvetica-Oblique", fontSize=6.6, fillColor=GREY, textAnchor=anchor))

    def badge(n, x, y):
        d.add(Rect(x - 6, y - 6, 12, 12, rx=6, ry=6, fillColor=ACCENT, strokeColor=ACCENT))
        d.add(String(x, y - 2.4, str(n), fontName="Helvetica-Bold", fontSize=7.4, fillColor=colors.white, textAnchor="middle"))

    # row 1: the watched pipeline
    box(0, 240, 96, 46, "Developer", ["pushes a commit"], people_f, people_l)
    box(124, 240, 130, 46, "Watched pipeline", ["GitHub Actions workflow", "unchanged; result: failure"], gh_f, gh_l)
    box(282, 240, 140, 46, "Trigger", ["workflow_run event,", "only when the run failed"], gh_f, gh_l)
    arrow([(96, 263), (124, 263)])
    arrow([(254, 263), (282, 263)])
    note(268, 268, "fails", "middle")

    # the triage job
    d.add(Rect(0, 118, W, 110, rx=7, ry=7, fillColor=tri_f, strokeColor=tri_l, strokeWidth=1.3))
    d.add(String(8, 215, "Triage job on a GitHub-hosted runner: read-only token, never runs the failed code",
                 fontName="Helvetica-Bold", fontSize=7.6, fillColor=colors.HexColor("#7A4B00")))
    arrow([(352, 240), (352, 228)])
    badge(1, 340, 234)
    box(10, 128, 92, 70, "Fetch logs", ["GitHub REST API,", "failed jobs only"])
    box(124, 128, 92, 70, "Clean + redact", ["timestamps removed,", "secret-shaped text", "blanked"])
    box(238, 128, 92, 70, "Rules", ["clear-cut failures:", "network, disk, rate", "limit, bad package"])
    box(352, 128, 124, 70, "LLM call", ["Claude Sonnet 5.5", "redacted excerpt (max", "12,000 chars) + metadata"], ant_f, ant_l)
    arrow([(102, 163), (124, 163)])
    arrow([(216, 163), (238, 163)])
    arrow([(330, 163), (352, 163)])
    badge(2, 22, 208)
    badge(3, 136, 208)
    badge(4, 250, 208)
    badge(5, 364, 208)
    note(341, 169, "else", "middle")

    # row 3: the result
    box(238, 38, 100, 56, "Diagnosis", ["category, confidence,", "evidence, next step"])
    box(124, 38, 92, 56, "Post to Slack", ["incoming webhook,", "text escaped"], slk_f, slk_l)
    box(0, 38, 96, 56, "On-call engineer", ["checks the evidence,", "decides and acts"], people_f, people_l)
    box(366, 38, 110, 56, "Save record", ["JSON run artifact:", "model, token counts"])
    arrow([(284, 128), (284, 94)])
    note(278, 110, "clear-cut: no model call", "end")
    arrow([(414, 128), (414, 110), (322, 110), (322, 94)])
    arrow([(238, 66), (216, 66)])
    arrow([(124, 66), (96, 66)])
    arrow([(338, 66), (366, 66)])
    badge(6, 228, 82)
    badge(7, 108, 100)

    # not built
    box(0, 0, 96, 30, "Verdict button", ["not built"], colors.white, colors.HexColor("#8C99AA"), dashed=True)
    arrow([(48, 38), (48, 30)], dashed=True)
    note(104, 11, "records correct / wrong, which the KPIs in section 8 need")
    return d


def main():
    out = Path(__file__).resolve().parent
    pdf = out / "architecture-diagram.pdf"
    inner = architecture_diagram()
    pad = 4
    padded = Drawing(inner.width + 2 * pad, inner.height + 2 * pad)
    group = Group(*inner.contents)
    group.translate(pad, pad)
    padded.add(group)
    renderPDF.drawToFile(padded, str(pdf))
    print(f"wrote {pdf.name}")
    try:
        import pypdfium2
    except ImportError:
        return
    pypdfium2.PdfDocument(str(pdf))[0].render(scale=3).to_pil().save(out / "architecture-diagram.png")
    print("wrote architecture-diagram.png")


if __name__ == "__main__":
    main()
