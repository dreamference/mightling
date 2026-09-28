# Builds puffin.pdf inside the TeX Live container, so no local TeX installation is needed.
IMAGE ?= texlive/texlive:latest
RUN = docker run --rm -u $$(id -u):$$(id -g) -v "$(CURDIR)":/work -w /work $(IMAGE)

puffin.pdf: puffin.tex references.bib
	$(RUN) latexmk -pdf -interaction=nonstopmode -halt-on-error puffin.tex

arxiv.tar.gz: puffin.pdf
	tar czf $@ puffin.tex references.bib puffin.bbl

clean:
	$(RUN) latexmk -C puffin.tex
	rm -f arxiv.tar.gz

.PHONY: clean
