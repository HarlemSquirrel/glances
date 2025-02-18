PORT?=8008
LASTTAG = $(shell git describe --tags --abbrev=0)

# if the command is only `make`, the default tasks will be the printing of the help.
.DEFAULT_GOAL := help

.PHONY: help
help: ## List all make commands available
	@grep -E '^[\.a-zA-Z_%-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk -F ":" '{print $1}' | grep -v % | sed 's/\\//g' | sort | awk 'BEGIN {FS = ":[^:]*?##"}; {printf "\033[1;34mmake %-50s\033[0m %s\n", $$1, $$2}'

# ===================================================================
# Virtualenv
# ===================================================================

venv-python: venv-full-python venv-min-python venv-dev-python ## Install all Python 3 venv

venv: venv-full venv-min venv-dev ## Install all Python 3 dependencies

venv-upgrade: venv-full-upgrade venv-min-upgrade venv-dev-upgrade ## Upgrade all Python 3 dependencies

# For full installation (with optional dependencies)

venv-full-python: ## Install Python 3 venv
	virtualenv -p /usr/bin/python3 venv

venv-full: venv-python ## Install Python 3 run-time dependencies
	./venv/bin/pip install -r requirements.txt
	./venv/bin/pip install -r optional-requirements.txt

venv-full-upgrade: ## Upgrade Python 3 run-time dependencies
	./venv/bin/pip install --upgrade pip
	./venv/bin/pip install --upgrade -r requirements.txt
	./venv/bin/pip install --upgrade -r optional-requirements.txt

# For minimal installation (without optional dependencies)

venv-min-python: ## Install Python 3 venv minimal
	virtualenv -p /usr/bin/python3 venv-min

venv-min: venv-min-python ## Install Python 3 minimal run-time dependencies
	./venv-min/bin/pip install -r requirements.txt

venv-min-upgrade: ## Upgrade Python 3 minimal run-time dependencies
	./venv-min/bin/pip install --upgrade pip
	./venv-min/bin/pip install --upgrade -r requirements.txt

# For development

venv-dev-python: ## Install Python 3 venv
	virtualenv -p /usr/bin/python3 venv-dev

venv-dev: venv-python ## Install Python 3 dev dependencies
	./venv-dev/bin/pip install -r dev-requirements.txt
	./venv-dev/bin/pip install -r doc-requirements.txt

venv-dev-upgrade: ## Upgrade Python 3 dev dependencies
	./venv-dev/bin/pip install --upgrade pip
	./venv-dev/bin/pip install --upgrade -r dev-requirements.txt
	./venv-dev/bin/pip install --upgrade -r doc-requirements.txt

# ===================================================================
# Tests
# ===================================================================

test-core: ## Run core unit tests
	./venv/bin/python ./unittest-core.py

test-restful: ## Run Restful unit tests
	./venv/bin/python ./unittest-restful.py

test-xmlrpc: ## Run XMLRPC unit tests
	./venv/bin/python ./unittest-xmlrpc.py

test: test-core test-restful test-xmlrpc ## Run unit tests

test-with-upgrade: venv-upgrade venv-dev-upgrade test ## Upgrade deps and run unit tests

test-min: ## Run core unit tests in minimal environment
	./venv-min/bin/python ./unittest-core.py

test-min-with-upgrade: venv-min-upgrade ## Upgrade deps and run unit tests in minimal environment
	./venv-min/bin/python ./unittest-core.py

# ===================================================================
# Linters, profilers and cyber security
# ===================================================================

format: ## Format the code
	@git ls-files 'glances/*.py' | xargs ./venv-dev/bin/python -m autopep8 --in-place --jobs 0 --global-config=.flake8
	@git ls-files 'glances/*.py' | xargs ./venv-dev/bin/python -m autoflake --in-place --remove-all-unused-imports --remove-unused-variables --remove-duplicate-keys --exclude="compat.py,globals.py"
	./venv-dev/bin/python -m black ./glances --exclude outputs/static

flake8: ## Run flake8 linter.
	@git ls-files 'glances/ *.py' | xargs ./venv-dev/bin/python -m flake8 --config=.flake8

ruff: ## Run Ruff (fastest) linter.
	./venv-dev/bin/python -m ruff check . --config=./pyproject.toml

codespell: ## Run codespell to fix common misspellings in text files
	./venv-dev/bin/codespell -S .git,./docs/_build,./Glances.egg-info,./venv*,./glances/outputs,*.svg -L hart,bu,te,statics

semgrep: ## Run semgrep to find bugs and enforce code standards
	./venv-dev/bin/semgrep scan --config=auto

profiling-gprof: ## Callgraph profiling (need "apt install graphviz")
	@echo "Start Glances for 30 iterations (more or less 1 mins, please do not exit !)"
	sleep 3
	./venv/bin/python -m cProfile -o ./glances.cprof ./run.py --stop-after 30
	./venv-dev/bin/gprof2dot -f pstats ./glances.cprof | dot -Tsvg -o ./docs/_static/glances-cgraph.svg
	rm -f ./glances.cprof

profiling-pyinstrument: ## PyInstrument profiling
	@echo "Start Glances for 30 iterations (more or less 1 mins, please do not exit !)"
	sleep 3
	./venv/bin/pip install pyinstrument
	./venv/bin/python -m pyinstrument -r html -o ./docs/_static/glances-pyinstrument.html -m glances --stop-after 30

profiling-pyspy: ## Flame profiling (currently not compatible with Python 3.12)
	@echo "Start Glances for 30 iterations (more or less 1 mins, please do not exit !)"
	sleep 3
	./venv-dev/bin/py-spy record -o ./docs/_static/glances-flame.svg -d 60 -s -- ./venv/bin/python ./run.py --stop-after 30

profiling: profiling-gprof profiling-pyinstrument profiling-pyspy ## Profiling of the Glances software

trace-malloc: ## Trace the malloc() calls
	@echo "Malloc test is running, please wait ~30 secondes..."
	./venv/bin/python -m glances -C ./conf/glances.conf --trace-malloc --stop-after 15 --quiet

memory-leak: ## Profile memory leaks
	./venv/bin/python -m glances -C ./conf/glances.conf --memory-leak

memory-profiling: ## Profile memory usage
	@echo "It's a very long test (~4 hours)..."
	rm -f mprofile_*.dat
	@echo "1/2 - Start memory profiling with the history option enable"
	./venv-dev/bin/mprof run -T 1 -C run.py -C ./conf/glances.conf --stop-after 2400 --quiet
	./venv-dev/bin/mprof plot --output ./docs/_static/glances-memory-profiling-with-history.png
	rm -f mprofile_*.dat
	@echo "2/2 - Start memory profiling with the history option disable"
	./venv-dev/bin/mprof run -T 1 -C run.py -C ./conf/glances.conf --disable-history --stop-after 2400 --quiet
	./venv-dev/bin/mprof plot --output ./docs/_static/glances-memory-profiling-without-history.png
	rm -f mprofile_*.dat

# Trivy installation: https://aquasecurity.github.io/trivy/latest/getting-started/installation/
trivy: ## Run Trivy to find vulnerabilities in container images
	trivy fs .

# ===================================================================
# Docs
# ===================================================================

docs: ## Create the documentation
	./venv/bin/python -m glances -C ./conf/glances.conf --api-doc > ./docs/api.rst
	cd docs && ./build.sh && cd ..

docs-server: docs ## Start a Web server to serve the documentation
	(sleep 2 && sensible-browser "http://localhost:$(PORT)") &
	cd docs/_build/html/ && ../../../venv/bin/python -m http.server $(PORT)

release-note: ## Generate release note
	git --no-pager log $(LASTTAG)..HEAD --first-parent --pretty=format:"* %s"
	@echo "\n"
	git --no-pager shortlog -s -n $(LASTTAG)..HEAD

install: ## Open a Web Browser to the installation procedure
	sensible-browser "https://github.com/nicolargo/glances#installation"

# ===================================================================
# WebUI
# Follow ./glances/outputs/static/README.md for more information
# ===================================================================

webui: ## Build the Web UI
	cd glances/outputs/static/ && npm ci && npm run build

webui-audit: ## Audit the Web UI
	cd glances/outputs/static/ && npm audit

webui-audit-fix: ## Fix audit the Web UI
	cd glances/outputs/static/ && npm audit fix && npm ci && npm run build

# ===================================================================
# Packaging
# ===================================================================

flatpak: venv-dev-upgrade ## Generate FlatPack JSON file
	git clone https://github.com/flatpak/flatpak-builder-tools.git
	./venv/bin/python ./flatpak-builder-tools/pip/flatpak-pip-generator glances
	rm -rf ./flatpak-builder-tools
	@echo "Now follow: https://github.com/flathub/flathub/wiki/App-Submission"

# Snap package is automaticaly build on the Snapcraft.io platform
# https://snapcraft.io/glances
# But you can try an offline build with the following command
snapcraft:
	snapcraft

# ===================================================================
# Docker
# Need Docker Buildx package (apt install docker-buildx on Ubuntu)
# ===================================================================

docker: docker-alpine docker-ubuntu ## Generate local docker images

docker-alpine: docker-alpine-full docker-alpine-minimal docker-alpine-dev ## Generate local docker images (Alpine)

docker-alpine-full: ## Generate local docker image (Alpine full)
	docker buildx build --target full -f ./docker-files/alpine.Dockerfile -t glances:local-alpine-full .

docker-alpine-minimal: ## Generate local docker image (Alpine minimal)
	docker buildx build --target minimal -f ./docker-files/alpine.Dockerfile -t glances:local-alpine-minimal .

docker-alpine-dev: ## Generate local docker image (Alpine dev)
	docker buildx build --target dev -f ./docker-files/alpine.Dockerfile -t glances:local-alpine-dev .

docker-ubuntu: docker-ubuntu-full docker-ubuntu-minimal docker-ubuntu-dev ## Generate local docker images (Ubuntu)

docker-ubuntu-full: ## Generate local docker image (Ubuntu full)
	docker buildx build --target full -f ./docker-files/ubuntu.Dockerfile -t glances:local-ubuntu-full .

docker-ubuntu-minimal: ## Generate local docker image (Ubuntu minimal)
	docker buildx build --target minimal -f ./docker-files/ubuntu.Dockerfile -t glances:local-ubuntu-minimal .

docker-ubuntu-dev: ## Generate local docker image (Ubuntu dev)
	docker buildx build --target dev -f ./docker-files/ubuntu.Dockerfile -t glances:local-ubuntu-dev .

# ===================================================================
# Run
# ===================================================================

run: ## Start Glances in console mode (also called standalone)
	./venv/bin/python -m glances -C ./conf/glances.conf

run-debug: ## Start Glances in debug console mode (also called standalone)
	./venv/bin/python -m glances -C ./conf/glances.conf -d

run-local-conf: ## Start Glances in console mode with the system conf file
	./venv/bin/python -m glances

run-local-conf-hide-public: ## Start Glances in console mode with the system conf file and hide public information
	./venv/bin/python -m glances --hide-public-info

run-min: ## Start minimal Glances in console mode (also called standalone)
	./venv-min/bin/python -m glances -C ./conf/glances.conf

run-min-debug: ## Start minimal Glances in debug console mode (also called standalone)
	./venv-min/bin/python -m glances -C ./conf/glances.conf -d

run-min-local-conf: ## Start minimal Glances in console mode with the system conf file
	./venv-min/bin/python -m glances

run-docker-alpine-minimal: ## Start Glances Alpine Docker minimal in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-alpine-minimal

run-docker-alpine-full: ## Start Glances Alpine Docker full in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-alpine-full

run-docker-alpine-dev: ## Start Glances Alpine Docker dev in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-alpine-dev

run-docker-ubuntu-minimal: ## Start Glances Ubuntu Docker minimal in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-ubuntu-minimal

run-docker-ubuntu-full: ## Start Glances Ubuntu Docker full in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-ubuntu-full

run-docker-ubuntu-dev: ## Start Glances Ubuntu Docker dev in console mode
	docker run --rm -e TZ="${TZ}" -e GLANCES_OPT="" -v /run/user/1000/podman/podman.sock:/run/user/1000/podman/podman.sock:ro -v /var/run/docker.sock:/var/run/docker.sock:ro --pid host --network host -it glances:local-ubuntu-dev

run-webserver: ## Start Glances in Web server mode
	./venv/bin/python -m glances -C ./conf/glances.conf -w

run-webserver-local-conf: ## Start Glances in Web server mode with the system conf file
	./venv/bin/python -m glances -w

run-webserver-local-conf-hide-public: ## Start Glances in Web server mode with the system conf file and hide public info
	./venv/bin/python -m glances -w --hide-public-info

run-restapiserver: ## Start Glances in REST API server mode
	./venv/bin/python -m glances -C ./conf/glances.conf -w --disable-webui

run-server: ## Start Glances in server mode (RPC)
	./venv/bin/python -m glances -C ./conf/glances.conf -s

run-client: ## Start Glances in client mode (RPC)
	./venv/bin/python -m glances -C ./conf/glances.conf -c localhost

run-browser: ## Start Glances in browser mode (RPC)
	./venv/bin/python -m glances -C ./conf/glances.conf --browser

run-issue: ## Start Glances in issue mode
	./venv/bin/python -m glances -C ./conf/glances.conf --issue

show-version: ## Show Glances version number
	./venv/bin/python -m glances -C ./conf/glances.conf -V

.PHONY: test docs docs-server venv venv-min venv-dev
