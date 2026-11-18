.PHONY: install sandbox validate check-python

IMAGE ?= fullhouse-sandbox:latest
PYTHON ?= python3

check-python:
	@$(PYTHON) -c 'import sys; v = sys.version_info[:2]; sys.exit(0 if v == (3, 10) else "This kit needs Python 3.10, but $(PYTHON) is %d.%d. See Getting started in the README (uv python install 3.10)." % v)'

install: check-python
	@echo ">> eval7 needs Cython<3 at build time and won't find it through build isolation"
	$(PYTHON) -m pip install "Cython<3"
	$(PYTHON) -m pip install --no-build-isolation eval7==0.1.7
	$(PYTHON) -m pip install -r requirements.txt

sandbox:
	@echo ">> Building the bot sandbox image (the same one the tournament uses)"
	docker build -t $(IMAGE) pokercore/sandbox

validate: check-python
	@if [ -z "$(BOT)" ]; then echo "usage: make validate BOT=my_bots/mybot"; exit 1; fi
	$(PYTHON) pokercore/validator.py $(BOT)
