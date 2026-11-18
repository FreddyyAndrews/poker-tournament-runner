.PHONY: install sandbox validate

IMAGE ?= fullhouse-sandbox:latest

install:
	@echo ">> eval7 needs Cython<3 at build time and won't find it through build isolation"
	pip3 install "Cython<3"
	pip3 install --no-build-isolation eval7==0.1.7
	pip3 install -r requirements.txt

sandbox:
	@echo ">> Building the bot sandbox image (the same one the tournament uses)"
	docker build -t $(IMAGE) pokercore/sandbox

validate:
	@if [ -z "$(BOT)" ]; then echo "usage: make validate BOT=my_bots/mybot"; exit 1; fi
	python3 pokercore/validator.py $(BOT)
