# CODEGRAPH.md — Semantic Code Intelligence & Guidelines

CodeGraph is a local-first, zero-config code intelligence tool and knowledge graph designed to supercharge AI coding agents (Claude, Gemini, Antigravity, Cursor, etc.). It indexes the codebase using tree-sitter, resolves symbol references, and builds a fast SQLite graph database locally.

By querying CodeGraph first, **agents avoid the "exploration tax" (consuming massive tokens on generic file scanning, grep, and find tool calls)**.

---

## 🚀 MANDATE FOR FUTURE AGENTS

Whenever you start a session or need to explore symbol relationships, **YOU MUST QUERY CODEGRAPH FIRST before running any grep, find, or multi-file reading commands**.

### Core Commands

Always run via `npx @colbymchenry/codegraph <command>`:

1. **Search for symbols (Functions, Classes, Variables, Methods, Imports):**
   ```bash
   npx @colbymchenry/codegraph query "symbol_name"
   ```
2. **Find callers of a function or method:**
   ```bash
   npx @colbymchenry/codegraph callers "function_name"
   ```
3. **Find callees (what a function calls):**
   ```bash
   npx @colbymchenry/codegraph callees "function_name"
   ```
4. **Analyze the impact radius of changing a symbol:**
   ```bash
   npx @colbymchenry/codegraph impact "symbol_name"
   ```
5. **Get project statistics & status:**
   ```bash
   npx @colbymchenry/codegraph status
   ```

---

## 🛠️ MAINTENANCE & AUTO-SYNCING

- **Zero-Config Exclusions:** CodeGraph respects your project's `.gitignore` file. It only indexes trading/backtest code and completely ignores logs, `.tmp`, backups, database files, and `klines_cache/`.
- **Syncing Changes:** If you add or modify files, the watcher automatically debounces and syncs the index. If you need to force a manual sync:
  ```bash
  npx @colbymchenry/codegraph sync
  ```
- **Stale Lock Fix:** If a process crashed and locked the database:
  ```bash
  npx @colbymchenry/codegraph unlock
  ```

---

## 🔴 PARITY

CodeGraph is initialized and fully indexed on **BOTH MacBook (`/Users/niels/Documents/binance`) and S1 Sandbox (`/home/niels/binance-sandbox`)**.
- Both indices are strictly kept in sync using the shared `.gitignore` rules.
- Do not run multi-year sweeps on MacBook. MacBook indexes live code; S1 indexes sweep sandbox.
