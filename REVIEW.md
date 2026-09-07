# News & Journalism Knowledge Graph Code Review

This document summarizes the findings from a thorough review of the `news-journalism-kg` codebase, categorized by severity.

## HIGH

### Security (Path Traversal Risk)
*   **File Path:** `workbench/server.py` (Line 153-164)
*   **Impact:** The `do_GET` handler for the `/api/csv` endpoint attempts to validate the requested file name using a simple check against `/` and `\`. However, unlike the `do_POST` endpoint for `/api/csv`, it lacks the more robust `safe_inside_root` verification. This inconsistency could potentially allow sophisticated path traversal attacks to bypass the basic slash check and expose arbitrary files on the host filesystem.
*   **Fix Suggestion:** Apply the `safe_inside_root(p)` check in the `do_GET` handler, identical to how it is used in the `do_POST` handler.

### Reliability (Synchronous Scripts / `document.write`)
*   **File Path:** `workbench/static/index.html` (Lines 16-21)
*   **Impact:** The HTML file relies on `document.write` to inject a fallback script tag for `vis-network.min.js` if the primary CDN fails. Using `document.write` for external scripts is highly discouraged in modern web development because it blocks HTML parsing and page rendering, and is blocked or delayed by many modern browsers.
*   **Fix Suggestion:** Replace `document.write` with dynamic script insertion (e.g., `const script = document.createElement('script'); script.src = '...'; document.head.appendChild(script);`) or, preferably, host the library locally within the repository to eliminate CDN dependency entirely.

### Test Coverage (Lack of Automated Testing)
*   **File Path:** Whole project (`scripts/validate_csv.py`, `workbench/server.py`, `workbench/static/app.js`)
*   **Impact:** The project currently lacks automated tests. Critical logic for validating data structures (`validate_csv.py`), serving the API (`server.py`), and rendering the frontend graph logic (`app.js`) are untested. This makes future modifications extremely risky and prone to regressions.
*   **Fix Suggestion:** Introduce a testing framework. Use `pytest` for backend python scripts and endpoints. Introduce a basic JS testing setup (like Jest or Mocha) for testing frontend logic, particularly data transformation and formatting functions.


## MEDIUM

### Architecture (Monolithic Frontend)
*   **File Path:** `workbench/static/app.js`
*   **Impact:** The entire frontend logic is bundled into a single JavaScript file containing over 1,300 lines of code. It tightly couples routing, global state (`DATA_CACHE`), rendering (mostly via string interpolation and `innerHTML`), API interactions, and initialization logic. As the application grows, this will become difficult to maintain, navigate, and scale.
*   **Fix Suggestion:** Refactor `app.js` into modular ECMAScript (ES) modules. Separate components into individual files (e.g., `api.js`, `router.js`, `views/*.js`, `utils.js`). Adopt a lightweight framework or structured component pattern for UI rendering.

### Performance (Redundant Disk I/O on Search)
*   **File Path:** `workbench/server.py` (Line 166-193)
*   **Impact:** The `/api/search` endpoint reads `scholars.csv`, `passages.csv`, and `propositions.csv` from disk entirely into memory on *every single request*. For large datasets, this approach is highly inefficient, leading to high latency and unnecessary CPU/disk overhead.
*   **Fix Suggestion:** Implement in-memory caching of the parsed CSV data on the server side (invalidating it when files are updated via POST), or migrate the backend logic to query an SQLite database instead of directly parsing CSVs.

### Reliability (Unhandled JSON Decode)
*   **File Path:** `workbench/server.py` (Line 109-114)
*   **Impact:** The `_read_json` method reads the exact `Content-Length` and blindly decodes the payload with `json.loads()`. It lacks a `try...except` block for `json.JSONDecodeError` or connection read errors. An invalid JSON payload will raise an unhandled exception, potentially crashing the request handler thread and returning a 500 error instead of a graceful 400 Bad Request.
*   **Fix Suggestion:** Wrap the `json.loads(raw.decode('utf-8'))` call in a `try...except json.JSONDecodeError` block. Return `{}` or raise a custom exception that is caught by the handler to return an HTTP 400 response.


## LOW

### Security (XSS Risks with `innerHTML`)
*   **File Path:** `workbench/static/app.js` (e.g., Line 968)
*   **Impact:** While `escapeHtml` is correctly used in many places to sanitize data from CSVs, there are instances where `innerHTML` is assigned dynamic content (like `err.message` during graph rendering). Although the current risk is low (since errors are generated internally), using `innerHTML` directly is generally a bad practice.
*   **Fix Suggestion:** Use `textContent` or `innerText` when inserting plain text or error messages. If rendering structured HTML is necessary, use DOM manipulation (e.g., `createElement`) or a sanitizer library (like DOMPurify).
