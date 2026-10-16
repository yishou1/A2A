# Repository Guidelines

## Project Structure & Module Organization
- `src/synapserag/`: core library (`SynapseRAG.py`, `StandardRAG.py`, `embedding_model/`, `llm/`, `prompts/`, `utils/`, `evaluation/`).
- `demo*.py`, `main*.py`, `reproduce/`: retained research entry points; datasets are not bundled.
- `api_server.py`: HTTP service entry point. Persistent data and environments must remain external; never delete them as part of source cleanup.
- `API_REFERENCE.md`, `DEPLOYMENT_A2A.md`: service interface and deployment documentation.

## Build, Test, and Development Commands
- Create environment and install dependencies:  
  `conda create -n synapserag python=3.10 && conda activate synapserag`  
  `pip install -r requirements.txt` or `pip install -e .`
- In this bundled A2A copy, use the external environment selected by `SYNAPSERAG_PYTHON` for scripts and tests. A dedicated Conda environment or the existing external `.venv` is supported. Set `PYTHONPATH` to this service directory and its `src` directory so imports use this copy, not an old editable installation.
- Quick demo with default OpenAI-style config:  
  `python demo.py`
- Dataset experiment (e.g., Musique):  
  `python main.py --dataset musique`
- Backend smoke tests:  
  OpenAI: `python tests_openai.py` · Azure: `python tests_azure.py` · Local/Transformers: `python tests_local.py`, `python test_transformers.py`

## Coding Style & Naming Conventions
- Python, PEP 8 style: 4-space indentation, imports grouped (stdlib, third-party, local).
- Use `snake_case` for functions/variables, `CamelCase` for classes, `UPPER_CASE` for constants.
- Add type hints and docstrings for new public APIs; keep modules focused (retrieval, indexing, evaluation, etc.).

## Testing Guidelines
- Prefer small, deterministic tests using tiny in-memory corpora (see `tests_openai.py` pattern).
- Keep backend-specific tests isolated so they can be skipped when credentials or hardware are missing.
- When adding features, update or add a corresponding `tests_*.py` script, or introduce a `pytest` test under `tests/`.

## Commit & Pull Request Guidelines
- Commit messages: short, imperative summaries consistent with existing history, e.g.:  
  `fix bugs on all_responses` · `add method to load transformers backends`
- PRs should state:
  - What changed and why (include affected entry points like `main.py`, `SynapseRAG.py`).
  - How you verified it (commands run, datasets used).
  - Any new configuration or env vars (`SYNAPSERAG_OPENAI_API_KEY`, `HF_HOME`, GPU requirements).
