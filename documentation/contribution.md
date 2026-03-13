# How to set up the project environment

1. Clone the repository
2. Install dependencies using `uv`:
    ```bash
    uv pip install -e ".[dev]"
    ```
3. Install and update pre-commit hooks:
    ```bash
    pre-commit install
    pre-commit autoupdate
    ```
    The pre-commit hooks run ruff (lint + format), pyrefly (type check), and pytest before each commit. A failing hook aborts the commit.
4. Run all checks manually:
    ```bash
    pre-commit run --all-files
    ```
5. If everything is green — you're ready to open a PR!
