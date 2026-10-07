# Builds mling.pdf inside the TeX Live container, so no local TeX installation is needed.
IMAGE ?= texlive/texlive:latest
RUN = docker run --rm -u $$(id -u):$$(id -g) -v "$(CURDIR)":/work -w /work $(IMAGE)

mling.pdf: mling.tex references.bib
	$(RUN) latexmk -pdf -interaction=nonstopmode -halt-on-error mling.tex

arxiv.tar.gz: mling.pdf
	tar czf $@ mling.tex references.bib mling.bbl

clean:
	$(RUN) latexmk -C mling.tex
	rm -f arxiv.tar.gz

.PHONY: clean
