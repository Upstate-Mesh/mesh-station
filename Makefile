.PHONY: run format

run:
	python main.py

format:
	black .
	isort --profile black .
	yamlfix .
