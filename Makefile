.PHONY: install run

install:
	uv pip install -r requirements.txt

run:
	python main.py