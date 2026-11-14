---
name: bruno-collections
description: Create, update, and troubleshoot Bruno API collections and requests. Use when editing Bruno collection files, organizing requests and folders, or diagnosing why requests do not appear in Bruno.
metadata:
  short-description: Work with Bruno collections
---

# Bruno Collections

Use this skill when a task changes Bruno collection structure, request definitions, environments, or collection troubleshooting.

## Workflow

1. Inspect the collection root and representative request, folder, and environment files before editing. Identify whether the collection uses OpenCollection YAML (`opencollection.yml`, `.yml`) or Bru format (`bruno.json`, `.bru`). Preserve its format and existing naming/layout conventions. Do not infer request format from the UI alone.
2. Read the relevant official Bruno format documentation below when a format detail is unclear or when the collection format is new to the task.
3. Derive request methods, paths, parameters, headers, authentication, and bodies from the API implementation or OpenAPI specification. Reuse the collection's environment variables; do not hard-code credentials or replace existing collection settings.
4. Add requests with meaningful names and sequence numbers that fit their folder. Add query/path parameters in the format's supported structure. Keep optional parameters optional and mark illustrative values as examples where needed.
5. Validate YAML syntax and required collection structure. Check `git diff` and `git status`; preserve unrelated edits. If Bruno CLI is installed, use its validation or run command only when it can run safely without sending requests to unintended services. Otherwise report that UI loading/runtime behavior was not confirmed.
6. When troubleshooting missing requests, compare the file extension and request schema with the collection format. Check that files are in the intended folder, are not ignored, and have valid metadata. Make the smallest correction and verify the resulting collection tree.

## OpenCollection YAML

Bruno 3+ uses OpenCollection YAML files with an `opencollection.yml` collection root, `.yml` request files, and `folder.yml` folder metadata. A request generally uses `info`, `http`, and optionally `runtime`, `settings`, and `docs`. HTTP query and path parameters belong under `http.params` as a list with `name`, `value`, and `type`. Use the documented schema rather than inventing properties. YAML requests and Bru requests may coexist in Bruno, but preserve the collection's existing format unless the user requests migration.

## Bru format

Bru collections use `bruno.json` and `.bru` files. Request files use Bru Lang blocks such as `meta`, an HTTP method block, and optional `params:query`, `headers`, `body`, `tests`, and `settings` blocks. Read the Bru Lang reference before creating less common constructs or scripts.

## Official references

- [OpenCollection YAML overview](https://docs.usebruno.com/opencollection-yaml/overview)
- [OpenCollection YAML structure reference](https://docs.usebruno.com/opencollection-yaml/structure-reference)
- [OpenCollection YAML samples](https://docs.usebruno.com/opencollection-yaml/samples)
- [Bru Markup Language](https://docs.usebruno.com/bru-lang/overview)
- [Bruno collection format and creation](https://docs.usebruno.com/get-started/bruno-basics/create-a-collection)
- [Bruno folder organization](https://docs.usebruno.com/get-started/bruno-basics/create-a-folder)
