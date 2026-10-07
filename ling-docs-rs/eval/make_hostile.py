"""Generate hostile PDFs for the extractor crash-resistance test (all synthetic, CC0)."""
import os
import random
import sys
import zlib

OUT = sys.argv[1]
SAMPLE = sys.argv[2]  # a real PDF to truncate
os.makedirs(OUT, exist_ok=True)


def pdf(objects, root=1, xref_ok=True):
    """Assemble a PDF from object bodies (1-based), with a correct (or deliberately wrong) xref."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{(off if xref_ok else off + 17):010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def stream(data, flate=False):
    if flate:
        return f"<< /Length {len(data)} /Filter /FlateDecode >>\nstream\n".encode() + data + b"\nendstream"
    return f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def one_page(content, flate=False):
    return pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >>"
                b" /Contents 4 0 R >>",
                stream(content, flate), FONT])


def write(name, data):
    open(os.path.join(OUT, name), "wb").write(data)
    print(name, len(data))


write("empty.pdf", b"")
real = open(SAMPLE, "rb").read()
write("truncated.pdf", real[: len(real) // 3])
rng = random.Random(7)
write("garbage.pdf", b"%PDF-1.7\n" + bytes(rng.getrandbits(8) for _ in range(200_000)))
write("bad-xref.pdf", pdf([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                           b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
                           b" /Resources << /Font << /F1 5 0 R >> >> >>",
                           stream(b"BT /F1 12 Tf 72 720 Td (bad xref) Tj ET"), FONT], xref_ok=False))
# ~1 GB of spaces in one content stream, compressed to about 1 MB.
comp = zlib.compressobj(9)
bomb = bytearray(comp.compress(b"BT /F1 12 Tf 72 720 Td (bomb) Tj ET\n"))
block = b" " * (1 << 20)
for _ in range(1024):
    bomb += comp.compress(block)
bomb += comp.flush()
write("flate-bomb.pdf", one_page(bytes(bomb), flate=True))
write("deep-nesting.pdf", one_page(b"BT /F1 12 Tf 72 720 Td " + b"[" * 100_000 + b"(x)" + b"]" * 100_000 + b" TJ ET"))
write("page-tree-loop.pdf", pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                                 b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>",
                                 b"<< /Type /Pages /Parent 2 0 R /Kids [2 0 R] /Count 1 >>"]))
kids = " ".join(f"{i} 0 R" for i in range(4, 4 + 20_000)).encode()
objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [" + kids + b"] /Count 20000 >>",
        stream(b"BT /F1 12 Tf 72 720 Td (page) Tj ET")]
objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R"
         b" /Resources << /Font << /F1 " + str(4 + 20_000).encode() + b" 0 R >> >> >>"] * 20_000
objs.append(FONT)
write("many-pages.pdf", pdf(objs))
ops = b"BT /F1 1 Tf " + b"0 1 Td (a) Tj " * 2_000_000 + b"ET"
write("text-ops.pdf", one_page(ops, flate=True and False))
try:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(os.path.join(OUT, "bad-xref.pdf"), strict=False))
    w.encrypt("secret-user-pw", algorithm="AES-256")
    with open(os.path.join(OUT, "encrypted.pdf"), "wb") as f:
        w.write(f)
    print("encrypted.pdf")
except Exception as exc:  # noqa: BLE001
    print("encrypted skipped", exc)
