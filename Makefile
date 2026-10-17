.PHONY: setup seed test mcp

setup:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

seed:
	python -m resolvelab.seed

test:
	python -m pytest -q

mcp:
	python -m resolvelab.mcp_server
