# Contributing to WhatIsMyTip

Thank you for your interest in contributing to WhatIsMyTip! We welcome contributions from the community.

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow. Please be respectful, inclusive, and constructive in all interactions.

## How to Contribute

### Reporting Bugs

1. Check if the issue has already been reported
2. Search for existing issues to avoid duplicates
3. Create a new issue with:
   - Clear, descriptive title
   - Detailed description of the bug
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Environment information (OS, browser, Python version, etc.)
   - Screenshots or logs if applicable

**Example Issue Template**:

```markdown
## Bug Report
**Title**: [Bug title]
**Environment**: Windows 11, Chrome, Python 3.11

**Description**:
[Detailed description of the bug]

**Steps to Reproduce**:
1. [Step 1]
2. [Step 2]
3. [Step 3]

**Expected Behavior**:
[What should happen]

**Actual Behavior**:
[What actually happens]

**Screenshots**:
[Attach screenshots if applicable]

**Logs**:
[Attach relevant logs]
```

### Suggesting Enhancements

1. Check if the enhancement has already been suggested
2. Create a new issue with:
   - Clear, descriptive title
   - Detailed description of the enhancement
   - Why this enhancement would be valuable
   - How it would improve the project

**Example Enhancement Template**:

```markdown
## Enhancement Suggestion
**Title**: [Enhancement title]

**Description**:
[Detailed description of the enhancement]

**Why this would be valuable**:
[Explain the benefits]

**Proposed Implementation**:
[How you think this could be implemented]

**Alternatives Considered**:
[Other approaches you considered]
```

### Submitting Code

#### Prerequisites

- Fork the repository
- Create a feature branch from `main`
- Make your changes
- Test thoroughly
- Commit with conventional commit messages
- Push to your fork
- Create a Pull Request

#### Development Setup

Follow the [Development Guide](docs/development.md) to set up your development environment.

#### Branch Naming

Use descriptive branch names:

- `feature/your-feature-name` - New features
- `fix/your-bug-fix` - Bug fixes
- `docs/your-documentation` - Documentation updates
- `refactor/your-refactor` - Code refactoring
- `test/your-test` - Test additions
- `chore/your-task` - Maintenance tasks

#### Commit Messages

Use conventional commit messages:

- `feat: add new ML model`
- `fix: resolve database connection issue`
- `docs: update API documentation`
- `refactor: improve code structure`
- `test: add unit tests`
- `style: format code`
- `chore: update dependencies`

#### Pull Request Process

1. **Open a Pull Request**:
   - Go to your fork on GitHub/GitLab
   - Click "New Pull Request"
   - Select your branch
   - Provide a clear description

2. **PR Description**:
   - Briefly describe the changes
   - Link to related issues
   - Explain the reasoning behind the changes
   - List any breaking changes

3. **Address Feedback**:
   - Respond to reviewer comments
   - Make requested changes
   - Update the PR description if needed

4. **Get Approval**:
   - Wait for at least one maintainer to review
   - Address any feedback
   - Get approval to merge

5. **Merge**:
   - Once approved, the PR can be merged
   - The maintainer will merge your changes

#### Code Review Checklist

Before submitting a PR, ensure:

**Frontend**:
- [ ] TypeScript types are correct
- [ ] ESLint passes without errors
- [ ] Components are properly scoped
- [ ] Responsive design works
- [ ] Accessibility features are present
- [ ] Code follows Nuxt conventions
- [ ] Tests pass (if applicable)

**Backend**:
- [ ] Type hints are present
- [ ] PEP 8 style guide is followed
- [ ] Ruff passes without errors
- [ ] Async/await is used correctly
- [ ] Error handling is present
- [ ] Documentation is complete
- [ ] Tests pass (if applicable)

**Documentation**:
- [ ] README is updated
- [ ] Code comments are clear
- [ ] API documentation is accurate
- [ ] Examples are provided

## Development Workflow

### 1. Fork and Clone

```bash
# Fork the repository on GitHub/GitLab
# Clone your fork
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd whatismytip
```

### 2. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 3. Make Changes

- Update frontend in `frontend/`
- Update backend in `backend/`
- Update documentation in `docs/`
- Write tests
- Add comments

### 4. Test Your Changes

```bash
# Frontend
cd frontend
bun run lint
bun run typecheck
bun run dev

# Backend
cd backend
uv run pytest
uv run uvicorn main:app --reload
```

### 5. Commit Changes

```bash
cd ..
git add .
git commit -m "feat: add your feature"
```

### 6. Push to Remote

```bash
git push origin feature/your-feature-name
```

### 7. Create Pull Request

1. Go to your fork on GitHub/GitLab
2. Click "New Pull Request"
3. Select your branch
4. Fill in the PR description
5. Submit the PR

## Project Structure

### Frontend (`frontend/`)

- **Components**: Reusable Vue components
- **Pages**: Route-specific pages
- **Composables**: Reusable logic
- **Assets**: Static files and styles

### Backend (`backend/`)

- **API**: API route handlers
- **CRUD**: Database operations
- **Models**: Database models
- **ML Models**: Machine learning models
- **Heuristics**: Prediction strategies
- **Services**: Business logic
- **Schemas**: Pydantic validation schemas

### Documentation (`docs/`)

- `backend.md` - Backend documentation
- `frontend.md` - Frontend documentation
- `deployment.md` - Deployment guide
- `development.md` - Development guide
- `api.md` - API reference

## Coding Standards

### Frontend

- Use TypeScript for type safety
- Follow Vue 3 Composition API
- Use Nuxt conventions
- Keep components small and focused
- Use composables for shared logic
- Follow ESLint rules

### Backend

- Use async/await for all I/O operations
- Follow PEP 8 style guide
- Use type hints for all functions
- Follow ruff rules
- Use dependency injection
- Implement proper error handling

### Documentation

- Keep documentation up to date
- Use clear and concise language
- Include code examples
- Document all public APIs

## Testing

### Frontend Testing

```bash
cd frontend
bun run lint          # Check for linting errors
bun run typecheck     # Check TypeScript types
bun run dev           # Run development server
```

### Backend Testing

```bash
cd backend
uv run pytest         # Run all tests
uv run pytest -v      # Run with verbose output
uv run pytest --cov   # Run with coverage
```

### Manual Testing

- Test all features manually in the browser
- Check browser console for errors
- Verify API responses
- Test responsive design

## Adding Tests

### Frontend Tests

Create test files in `frontend/tests/`:

```typescript
// frontend/tests/example.test.ts
import { describe, it, expect } from 'vitest'

describe('Example Test', () => {
  it('should pass', () => {
    expect(true).toBe(true)
  })
})
```

### Backend Tests

Create test files in `backend/tests/`:

```python
# backend/tests/test_example.py
import pytest

@pytest.mark.asyncio
async def test_example():
    assert True
```

## Documentation

### Updating Documentation

When contributing:

1. Update relevant documentation files
2. Keep examples up to date
3. Add new sections if needed
4. Use clear and concise language
5. Include code examples

### Documentation Files

- `README.md` - Project overview
- `CONTRIBUTING.md` - This file
- `docs/backend.md` - Backend documentation
- `docs/frontend.md` - Frontend documentation
- `docs/deployment.md` - Deployment guide
- `docs/development.md` - Development guide
- `docs/api.md` - API reference

## Pull Request Guidelines

### PR Title

Use conventional commit style:

- `feat: add new ML model`
- `fix: resolve database connection issue`
- `docs: update API documentation`
- `refactor: improve code structure`

### PR Description

Include:

- Brief summary of changes
- Link to related issues
- Explanation of changes
- Any breaking changes
- Screenshots (if applicable)

### PR Checklist

Before submitting:

- [ ] Code follows coding standards
- [ ] Tests pass
- [ ] Documentation is updated
- [ ] Commit messages are clear
- [ ] No merge conflicts
- [ ] Branch is up to date with main

### Review Process

1. Reviewers will provide feedback
2. Address all comments
3. Make requested changes
4. Update PR description if needed
5. Request re-review

## Getting Help

If you need help:

1. Check the documentation
2. Search existing issues
3. Ask in the project's community
4. Open a new issue with details

## License

By contributing to WhatIsMyTip, you agree that your contributions will be licensed under the project's license.

## Acknowledgments

Thank you for contributing to WhatIsMyTip! Your contributions help make this project better for everyone.

## Questions?

If you have questions about contributing:

1. Check the [Development Guide](docs/development.md)
2. Review this file
3. Open an issue
4. Contact the maintainers

---

**Happy Contributing! 🚀**
