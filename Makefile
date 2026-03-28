WORKSPACE ?= /workspace
PYTEST_FLAGS ?= -q

.PHONY: test-all test-control-plane test-gpu-worker test-validator

test-all: test-control-plane test-gpu-worker test-validator

test-control-plane:
	docker compose run --rm --no-deps -v $(CURDIR):$(WORKSPACE) -w $(WORKSPACE) control-plane sh -lc "pip install --no-cache-dir -r apps/control-plane/requirements-dev.txt && PYTHONPATH=$(WORKSPACE)/apps/control-plane pytest $(PYTEST_FLAGS) apps/control-plane/tests"

test-gpu-worker:
	docker compose run --rm --no-deps -v $(CURDIR):$(WORKSPACE) -w $(WORKSPACE) gpu-worker sh -lc "pip install --no-cache-dir -r apps/gpu-worker/requirements-dev.txt && pytest $(PYTEST_FLAGS) apps/gpu-worker/tests"

test-validator:
	docker compose run --rm --no-deps -v $(CURDIR):$(WORKSPACE) -w $(WORKSPACE) control-plane sh -lc "pip install --no-cache-dir -r data/validator/requirements-dev.txt && pytest $(PYTEST_FLAGS) data/validator/tests"
