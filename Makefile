.PHONY: help venv install mypy isort lint clean

PYENV_EXISTS := $(shell command -v pyenv 2>/dev/null || echo "")

ifeq ($(PYENV_EXISTS),)
$(error pyenv is required but not found in PATH)
endif

PYTHON = $(HOME)/.pyenv/shims/python
PIP = $(HOME)/.pyenv/shims/pip

help:
	@echo "Usage:"
	@echo "  make venv        - It'll create virtual environment (.venv), if it has not been created still"
	@echo "  make install     - Project installation in editable mode with dev dependencies"
	@echo "  make mypy        - It'll run mypy on collector"
	@echo "  make isort       - It'll run isort on collector"
	@echo "  make lint        - Run mypy and isort together"
	@echo "  make clean       - Deleting __pycache__ a .mypy_cache"
	@echo "  make run         - Running money collection :-)"

.python-version:
	pyenv install -s 3.13
	pyenv virtualenv 3.13 money-collector-3.13
	pyenv local money-collector-3.13

venv: .python-version

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "Project installed in editable mode with dev dependencies"

mypy:
	$(PYTHON) -m mypy collector

isort:
	$(PYTHON) -m isort collector

lint: mypy isort

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .mypy_cache
	@echo "Cleaned __pycache__ and .mypy_cache"

run: venv
	$(PYTHON) -m collector
