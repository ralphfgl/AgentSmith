EVAL_DIR = evaluations

clean:
	@find . -name "__pycache__" -type d -exec rm -rf {} +
	rm -rf src/.mypy_cache

lint:
	flake8 src/
	mypy src/ --warn-return-any --warn-unused-ignores --ignore-missing-imports --disallow-untyped-defs --check-untyped-defs

.PHONY: clean
