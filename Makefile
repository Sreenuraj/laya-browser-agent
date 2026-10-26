.PHONY: check test lint pycompile self-check bench

PYTHON ?= .venv/bin/python
FLAKE8 ?= .venv/bin/flake8
PYTEST ?= .venv/bin/pytest

check: pycompile lint self-check

pycompile:
	@echo "==> Checking Python syntax compilation..."
	@$(PYTHON) -m py_compile main.py $$(find laya_browser_agent tests bench -name '*.py' 2>/dev/null)
	@echo "    ✓ Python compilation clean."

lint:
	@echo "==> Running flake8 code quality gate..."
	@$(FLAKE8) --select=E9,F63,F7,F82,F821,F401,F841 laya_browser_agent/ tests/ main.py $$(if [ -d bench ]; then echo bench/; fi)
	@echo "    ✓ Flake8 gate passed with zero errors/warnings."

self-check:
	@echo "==> Running T1 deterministic self-check suite..."
	@$(PYTHON) -m pytest tests/test_self_check.py tests/test_benchmark_oracles.py tests/test_phase0_tooling.py -v
	@echo "    ✓ T1 self-check suite green."

test:
	@$(PYTEST) tests/

bench:
	@$(PYTHON) -m bench run --tier T2 --repeat 1
