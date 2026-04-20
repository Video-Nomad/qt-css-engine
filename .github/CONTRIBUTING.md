## Contributing

Contributions are welcome. Make sure to run the tests and check the demo app and all widgets before submitting a pull request.
Tests do not cover all the cases so visual confirmation is required.
Keep in mind that there might be big changes to the codebase so prepare to rebase your branch if needed.
Messy commit histories will be squashed before merging.

PR names and commit messages are in the format: `type(scope): short description`

Extra details must be added to the commit message after a newline so:

```text
fix(css_parser): fix parsing of :hover pseudo-class

- :hover pseudo-class was not being parsed correctly in some cases
- added tests to cover that case
- more details here
```

## Dev commands

```bash
# Install dependencies and run the demo
uv sync --all-extras
uv run ./demo/main.py

# Run the test suite
uv run pytest

# Check and format the code
uvx ruff check --fix
uvx ruff format
uvx basedpyright
```
