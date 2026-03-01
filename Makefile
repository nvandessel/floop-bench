# Container runtime: prefer podman, fall back to docker
CONTAINER_RT := $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
IMAGE        := floop-sandbox
FLOOP_VERSION ?= 0.10.0

# Phase defaults
BUDGET  ?= 55
WORKERS ?= 1
TIMEOUT ?= 300
ARM     ?=

# Resolve ARM flag
ifdef ARM
  ARM_FLAG := --arm $(ARM)
else
  ARM_FLAG :=
endif

.PHONY: build shell smoke train eval leakage reset clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build: ## Build the sandbox container image
	$(CONTAINER_RT) build -t $(IMAGE) \
		--build-arg FLOOP_VERSION=$(FLOOP_VERSION) .

shell: ## Drop into an interactive sandbox shell
	$(CONTAINER_RT) run --rm -it \
		--cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
		--memory 2g --cpus 2 --pids-limit 256 \
		-v $$(pwd):/host:ro \
		-e GEMINI_API_KEY -e ANTHROPIC_API_KEY -e OPENAI_API_KEY \
		--entrypoint /bin/bash \
		$(IMAGE)

smoke: build ## Run smoke phase (2 tasks, sandboxed)
	uv run python -m harness.orchestrator --phase smoke \
		--budget $(BUDGET) --workers $(WORKERS) --timeout $(TIMEOUT) $(ARM_FLAG)

train: build ## Run train phase (30 tasks, sandboxed)
	uv run python -m harness.orchestrator --phase train \
		--budget $(BUDGET) --workers $(WORKERS) --timeout $(TIMEOUT) $(ARM_FLAG)

eval: build ## Run eval phase (20 tasks, sandboxed, leakage audit auto-runs)
	uv run python -m harness.orchestrator --phase eval \
		--budget $(BUDGET) --workers $(WORKERS) --timeout $(TIMEOUT) $(ARM_FLAG)

leakage: ## Run leakage audit against train volume
	uv run python -m scripts.check_leakage --volume floop-train

reset: ## Clear results DB, predictions, transcripts, and floop volumes
	rm -f results/results.db
	rm -rf results/predictions results/transcripts
	-$(CONTAINER_RT) volume rm floop-smoke floop-train floop-eval 2>/dev/null

clean: reset ## Reset + remove sandbox image
	-$(CONTAINER_RT) rmi $(IMAGE) 2>/dev/null
