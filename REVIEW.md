# News & Journalism Knowledge Graph Code Review

This document summarizes the findings from a thorough review of the `news-journalism-kg` codebase, categorized by severity.

## HIGH

### Security (Local Path Traversal Risk)
*   **File Path:** `workbench/server.py`
*   **Impact:** The `do_GET` handler for the `/static/` endpoint constructs file paths dynamically from the URL. Because `urlparse` does not collapse `..`, a request like `GET /static/../../config.json` can traverse outside the static directory, exposing arbitrary files on the local filesystem.
*   **Fix Suggestion:** Enforce `safe_inside_root(f, STATIC_DIR)` before confirming file existence and returning the content. Return a 404 response if the check fails.

### Security (Production Hash XSS)
*   **File Path:** `workbench/static/app.js` (Line 602, 1208)
*   **Impact:** Without a Content Security Policy (CSP), directly reflecting the URL hash in error messages (like `Scholar not found: ${id}` or `Route: ${route}`) can lead to Cross-Site Scripting (XSS). For example: `/#scholar/<img src=x onerror=alert(1)>`.
*   **Fix Suggestion:** Wrap all dynamic URL variables included in HTML templates with `escapeHtml()`.

### Security (Unescaped CSV Fields)
*   **File Path:** `workbench/static/app.js` (e.g., Line 768)
*   **Impact:** Certain CSV fields injected directly into HTML layout logic, such as map coordinates or tooltip values (`name_zh`, `active_year`), are missing HTML escaping, introducing XSS risks.
*   **Fix Suggestion:** Ensure `escapeHtml()` is applied consistently to all dynamic data from CSVs before inserting it into the DOM.


## MEDIUM

### Architecture (Monolithic Frontend)
*   **File Path:** `workbench/static/app.js`
*   **Impact:** The entire frontend logic is bundled into a single JavaScript file containing over 1,300 lines of code. It tightly couples routing, global state (`DATA_CACHE`), rendering (mostly via string interpolation and `innerHTML`), API interactions, and initialization logic. As the application grows, this will become difficult to maintain, navigate, and scale.
*   **Fix Suggestion:** Refactor `app.js` into modular ECMAScript (ES) modules. Separate components into individual files (e.g., `api.js`, `router.js`, `views/*.js`, `utils.js`). Adopt a lightweight framework or structured component pattern for UI rendering.

### Performance (Redundant Disk I/O on Browse)
*   **File Path:** `workbench/server.py` (Line 196)
*   **Impact:** The true performance hotspot is the `/api/browse` endpoint, which is hit continuously by the frontend. This endpoint reads 6 CSV files (`scholars.csv`, `passages.csv`, `propositions.csv`, etc.) entirely from disk into memory on every page load.
*   **Fix Suggestion:** Implement in-memory caching of the parsed CSV data on the server side (invalidating it when files are updated via POST), or migrate the backend logic to query an SQLite database instead of directly parsing CSVs.

### Reliability (Unhandled JSON Decode)
*   **File Path:** `workbench/server.py` (Line 109-114)
*   **Impact:** The `_read_json` method reads the exact `Content-Length` and blindly decodes the payload with `json.loads()`. It lacks validation for excessive file limits, UTF-8 constraints, or bad JSON format. An invalid payload triggers a 500 server crash, dropping connection instead of gracefully returning a client error.
*   **Fix Suggestion:** Wrap the decoding in a `try...except json.JSONDecodeError` block and enforce a sane maximum length for `Content-Length`. Explicitly check for these conditions and return an HTTP 400 response.


## LOW

### Reliability (Synchronous Scripts / `document.write`)
*   **File Path:** `workbench/static/index.html` (Lines 16-21)
*   **Impact:** The HTML file relies on `document.write` to inject a fallback script tag for `vis-network.min.js` if the primary CDN fails. This synchronous parsing stage limits modern browser capabilities.
*   **Fix Suggestion:** Replace `document.write` with dynamic script insertion or host the library locally.

### Quality / Process (Lack of Automated Testing)
*   **File Path:** Whole project
*   **Impact:** A small academic project without testing runs the risk of brittle changes and regressions in validation or graph rendering.
*   **Fix Suggestion:** Introduce a testing framework (e.g., `pytest` for python scripts and Jest for JS).

### Security (XSS Risks with `err.message`)
*   **File Path:** `workbench/static/app.js` (Line 968)
*   **Impact:** Rendering error messages directly via `innerHTML` is an unnecessary sink. While the severity is very low, as these messages are largely generated internally, it is generally bad practice.
*   **Fix Suggestion:** Use `textContent` or `innerText` when inserting plain text or error messages.
