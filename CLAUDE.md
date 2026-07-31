# Project instructions

## Playground folder

- `playground/` is the designated area for temporary scripts, ad hoc experiments, probes, and testing-related files.
- When you need to write a throwaway script, test harness, comparison experiment, or any file that is not part of the actual application code, create it inside `playground/` — not in the service directories (`apps/`) and not in system temp directories.
- Files already in `playground/` are ad hoc/experimental; do not treat them as production code or import from them in the services.
