EVAL_DIR = evaluations

clean:
	@rm -rf $(EVAL_DIR)
	@find . -name "__pycache__" -type d -exec rm -rf {} +

.PHONY: clean
