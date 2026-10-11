EVAL_DIR = evaluations

clean:
	@find . -name "__pycache__" -type d -exec rm -rf {} +

setup:
	systemctl --user enable --now podman.socket
	export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"
	podman unshare


.PHONY: clean
