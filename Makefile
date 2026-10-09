# One command: `make` (creates .venv, builds both scenarios, runs every test, checks the README numbers)
PYTHON ?= python3
VENV   ?= .venv
PY     ?= $(VENV)/bin/python
DBT    ?= $(VENV)/bin/dbt

.PHONY: all setup ci clean defective scenario-% test readme readme-check bigquery-compile

all: setup ci

setup:
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt

ci: clean defective test readme-check

clean: scenario-clean
defective: scenario-defective

scenario-%:
	$(PY) -m generator.generate --scenario $* --out warehouse/$*.duckdb
	DBT_DUCKDB_PATH=warehouse/$*.duckdb $(DBT) build --profiles-dir . --target-path target/$*
	$(PY) -m harness.check --scenario $* --db warehouse/$*.duckdb --run-results target/$*/run_results.json
	DBT_DUCKDB_PATH=warehouse/$*.duckdb $(DBT) compile --profiles-dir . --target-path target/analyses-$* --select "resource_type:analysis" --quiet

test:
	$(PY) -m pytest -q pytests

readme:
	$(PY) scripts/readme_results.py --write

readme-check:
	$(PY) scripts/readme_results.py --check

# Compile for BigQuery with a fake project and no connection, then check the SQL statically.
# Needs: pip install -r requirements-bigquery.txt
bigquery-compile:
	$(DBT) compile --profiles-dir ci --target-path target/bigquery --no-populate-cache --no-introspect --quiet
	$(PY) ci/check_bigquery_sql.py
