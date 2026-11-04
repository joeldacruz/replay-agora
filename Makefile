.PHONY: setup test e2e dev seed migrate
PY=.venv/bin/python
setup:
	python3 -m venv .venv && .venv/bin/pip install -r services/api/requirements-dev.txt
test:
	$(PY) -m pytest -q
e2e:
	scripts/e2e.sh
migrate:
	cd services/api && PYTHONPATH=. ../../$(PY) -m alembic upgrade head
seed:
	PYTHONPATH=services/api $(PY) -m app.seed
dev:
	@echo "Terminal 1: make migrate && make seed && PYTHONPATH=services/api:services/media-worker $(PY) -m uvicorn app.main:app --reload"
	@echo "Terminal 2: PYTHONPATH=services/api:services/media-worker $(PY) -m worker"
