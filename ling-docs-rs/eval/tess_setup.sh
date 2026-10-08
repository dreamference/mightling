#!/bin/bash
# Tesseract 5.3.4 from the distribution's packages, unpacked into scratch (nothing installed system-wide),
# plus tessdata_fast models (Apache-2.0) for the languages the OCR fixtures use.
D="$(dirname "$(readlink -f "$0")")"
T="$D/tess"
mkdir -p "$T/debs" "$T/root" "$T/tessdata"
cd "$T/debs" || exit 1
apt-get download tesseract-ocr=5.3.4-1build5 libtesseract5=5.3.4-1build5 liblept5 2>&1 | tail -1
for f in *.deb; do dpkg -x "$f" "$T/root"; done
# The output configs (tsv, hocr …) live beside the system tessdata; TESSDATA_PREFIX points here instead,
# and without them "tesseract … tsv" prints nothing (the first Tesseract run scored 0 everywhere).
cp -r "$T/root/usr/share/tesseract-ocr/5/tessdata/configs" "$T/tessdata/"
for l in eng deu swe fra rus chi_sim osd; do
  [ -f "$T/tessdata/$l.traineddata" ] || curl -sL -o "$T/tessdata/$l.traineddata" \
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata"
done
ls -la "$T/tessdata"
LD_LIBRARY_PATH="$T/root/usr/lib/aarch64-linux-gnu" "$T/root/usr/bin/tesseract" --version 2>&1 | head -2
LD_LIBRARY_PATH="$T/root/usr/lib/aarch64-linux-gnu" ldd "$T/root/usr/bin/tesseract" | grep "not found"
