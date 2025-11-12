# Contributing to Shunyalabs Python SDK

Thank you for your interest in contributing to the Shunyalabs Python SDK!

## Development Setup

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/shunyalabs-python-sdk.git
   cd shunyalabs-python-sdk
   ```

3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

4. Install development dependencies:
   ```bash
   make install-dev
   ```

5. Install pre-commit hooks:
   ```bash
   pre-commit install
   ```

## Making Changes

1. Create a new branch for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and test them:
   ```bash
   # Run tests
   make test-all
   
   # Run linting
   make lint-all
   
   # Run type checking
   make type-check-all
   ```

3. Format your code:
   ```bash
   make format-all
   ```

4. Commit your changes:
   ```bash
   git add .
   git commit -m "Description of your changes"
   ```

5. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

6. Create a Pull Request on GitHub

## Code Style

- Follow PEP 8 style guidelines
- Use type hints for all function signatures
- Write docstrings for all public functions and classes
- Run `make format-all` before committing

## Testing

- Add tests for new features
- Ensure all existing tests pass
- Run `make test-all` before submitting a PR

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

