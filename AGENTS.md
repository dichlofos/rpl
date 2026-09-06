# Repository guidance

## User-facing documentation

- Keep the `README.md` section `Возможности` synchronized with the application.
- When adding, changing, or removing user-facing functionality, update that section in the same change.
- Describe only functionality that is already implemented; keep the list concise and avoid roadmap items.
- Pure refactoring, internal maintenance, and bug fixes that do not change observable behavior do not require a README update.

## Git workflow

- This is a single-developer project. Commit completed changes and push them directly to the repository; do not create pull requests.
- Treat a user request to "create a PR" or "make a PR" as a request to commit the changes and push them to the repository.
