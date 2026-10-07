# Builds ling.pdf inside the TeX Live container, so no local TeX installation is needed.
IMAGE ?= texlive/texlive:latest
RUN = docker run --rm -u $$(id -u):$$(id -g) -v "$(CURDIR)":/work -w /work $(IMAGE)

ling.pdf: ling.tex references.bib
	$(RUN) latexmk -pdf -interaction=nonstopmode -halt-on-error ling.tex

arxiv.tar.gz: ling.pdf
	tar czf $@ ling.tex references.bib ling.bbl

clean:
	$(RUN) latexmk -C ling.tex
	rm -f arxiv.tar.gz

.PHONY: clean
