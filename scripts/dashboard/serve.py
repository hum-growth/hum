#!/usr/bin/env python3
from __future__ import annotations
"""
serve.py — Local dashboard server for hum.

Serves the dashboard SPA and JSON API endpoints for browsing feed items,
knowledge articles, ideas, content drafts, loop runs, and learnings.

Usage:
    python3 scripts/dashboard/serve.py                # http://localhost:8400
    python3 scripts/dashboard/serve.py --port 9000    # custom port
    python3 scripts/dashboard/serve.py --open          # auto-open browser
    python3 scripts/dashboard/serve.py --rebuild-index # force rebuild knowledge index
"""

import argparse
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config

_CFG = load_config()
_DATA_DIR: Path = _CFG["data_dir"]
_DASHBOARD_HTML = Path(__file__).resolve().parent / "index.html"

# ── In-memory state ──────────────────────────────────────────────────────────

_feed_items: list = []
_feed_sources: dict = {}
_ideas: list = []
_knowledge_sources: list = []  # parsed from index.md
_knowledge_index: list = []    # article metadata (no body)


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ! error loading {path}: {e}")
        return default if default is not None else []


def _load_feed():
    global _feed_items, _feed_sources
    raw = _load_json(_CFG["feeds_file"], [])
    _feed_items = raw if isinstance(raw, list) else raw.get("items", [])
    src = _load_json(_CFG["sources_file"], {})
    _feed_sources = src if isinstance(src, dict) else {"feed_sources": []}
    if "feed_sources" not in _feed_sources:
        _feed_sources["feed_sources"] = []


def _load_ideas():
    global _ideas
    ideas_file = _CFG["ideas_dir"] / "ideas.json"
    raw = _load_json(ideas_file, [])
    _ideas = raw if isinstance(raw, list) else raw.get("ideas", [])


def _save_sources(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _sources_add(body: dict) -> tuple[bool, str]:
    src_type = body.get("type", "")
    path = _CFG["sources_file"]
    sources = _load_json(path, {"feed_sources": []})
    if "feed_sources" not in sources:
        sources["feed_sources"] = []

    if src_type == "x":
        handle = body.get("handle", "").lstrip("@").strip()
        if not re.match(r'^[A-Za-z0-9_]{1,15}$', handle):
            return False, f"Invalid handle: {handle}"
        if any(s.get("handle", "").lower() == handle.lower()
               for s in sources["feed_sources"] if s.get("type") == "x_profile"):
            return False, f"@{handle} already exists"
        sources["feed_sources"].append({
            "type": "x_profile", "handle": handle,
            "category": body.get("category", ""), "description": "", "last_crawled": None,
        })
        _save_sources(path, sources)
        return True, f"Added @{handle}"

    elif src_type == "youtube":
        url = body.get("url", "").strip()
        name = body.get("name", "").strip() or url
        if not ("youtube.com" in url or "youtu.be" in url or url.startswith("@")):
            return False, f"Invalid YouTube URL or handle: {url}"
        if any(s.get("url", "").lower() == url.lower()
               for s in sources["feed_sources"] if s.get("type") == "youtube"):
            return False, f"{url} already exists"
        sources["feed_sources"].append({
            "type": "youtube", "name": name, "url": url, "description": "", "last_crawled": None,
        })
        _save_sources(path, sources)
        return True, f"Added YouTube: {name}"

    elif src_type == "website":
        name = body.get("name", "").strip()
        url = body.get("url", "").strip()
        if not name or not url:
            return False, "Name and URL are required"
        if any(s.get("name", "").lower() == name.lower()
               for s in sources["feed_sources"] if s.get("type") == "website"):
            return False, f"{name} already exists"
        sources["feed_sources"].append({
            "type": "website", "name": name, "url": url, "last_crawled": None,
        })
        _save_sources(path, sources)
        return True, f"Added website: {name}"

    else:
        return False, f"Unknown type: {src_type}"


def _sources_remove(body: dict) -> tuple[bool, str]:
    target = body.get("target", "").lstrip("@").strip().rstrip("/").lower()
    path = _CFG["sources_file"]
    sources = _load_json(path, {"feed_sources": []})
    if "feed_sources" not in sources:
        return False, "No sources"

    for i, s in enumerate(sources["feed_sources"]):
        match = False
        if s.get("type") == "x_profile" and s.get("handle", "").lower() == target:
            match = True
        elif s.get("type") == "youtube" and (
            s.get("name", "").lower() == target or s.get("url", "").lower() == target
        ):
            match = True
        elif s.get("type") == "website" and s.get("name", "").lower() == target:
            match = True
        if match:
            removed = sources["feed_sources"].pop(i)
            _save_sources(path, sources)
            label = removed.get("handle") or removed.get("name") or removed.get("url")
            return True, f"Removed {label}"

    return False, f"Not found: {target}"


def _knowledge_add(body: dict) -> tuple[bool, str]:
    key = re.sub(r'[^a-z0-9_-]', '', body.get("key", "").strip().lower().replace(" ", "_"))
    name = body.get("name", "").strip()
    handler = body.get("handler", "").strip().lower()
    url = body.get("url", "").strip()
    author = body.get("author", "").strip()

    if not key or not handler or not url:
        return False, "Key, handler, and feed URL are required"
    if handler not in ("rss", "sitemap", "youtube", "podcast"):
        return False, "Handler must be one of: rss, sitemap, youtube, podcast"

    idx_file = _CFG["knowledge_dir"] / "index.md"
    if not idx_file.exists():
        return False, "knowledge/index.md not found"

    content = idx_file.read_text(encoding="utf-8")
    if re.search(rf'^\|\s*`?{re.escape(key)}`?\s*\|', content, re.MULTILINE):
        return False, f"Key '{key}' already exists"

    lines = content.splitlines()
    last_table_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('|') and not re.match(r'^\s*\|[\s\-:|]+\|', line):
            last_table_line = i

    new_row = f"| {key} | {name} | {author} | {handler} | {url} |"
    if last_table_line >= 0:
        lines.insert(last_table_line + 1, new_row)
    else:
        lines += ["", "## Added via Dashboard", "",
                  "| Key | Source | Author | Handler | Feed URL |",
                  "|-----|--------|--------|---------|----------|",
                  new_row]

    idx_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True, f"Added {name or key}"


def _knowledge_remove(body: dict) -> tuple[bool, str]:
    key = body.get("key", "").strip().lower()
    if not key:
        return False, "Key is required"

    idx_file = _CFG["knowledge_dir"] / "index.md"
    if not idx_file.exists():
        return False, "knowledge/index.md not found"

    content = idx_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = [l for l in lines if not re.match(rf'^\|\s*`?{re.escape(key)}`?\s*\|', l)]
    if len(new_lines) == len(lines):
        return False, f"Key '{key}' not found"

    idx_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True, f"Removed {key}"


# ── Knowledge index ──────────────────────────────────────────────────────────

REQUIRED_COLS = {"key", "handler", "feed url"}


def _parse_knowledge_index_md() -> list:
    """Parse knowledge/index.md tables into source dicts."""
    idx_file = _CFG["knowledge_dir"] / "index.md"
    if not idx_file.exists():
        return []
    md = idx_file.read_text(encoding="utf-8")
    rows = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and re.match(
            r"^\s*\|?[\s\-:|]+\|[\s\-:|]+", lines[i + 1]
        ):
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            headers = [h.strip().lower().replace("&", "and") for h in header_cells]
            if not REQUIRED_COLS.issubset(set(headers)):
                i += 2
                continue
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if len(cells) != len(headers):
                    j += 1
                    continue
                row = {headers[k]: cells[k].strip("`").strip() for k in range(len(headers))}
                if row.get("key") and row.get("handler") and row.get("feed url"):
                    rows.append(row)
                j += 1
            i = j
            continue
        i += 1

    sources = []
    seen = set()
    for row in rows:
        key = row["key"]
        if key in seen:
            continue
        seen.add(key)
        name = row.get("name") or row.get("source") or row.get("name / source") or key
        sources.append({
            "key": key,
            "name": name,
            "handler": row["handler"].lower(),
            "url": row["feed url"],
        })
    return sources


def _parse_frontmatter(text: str) -> dict | None:
    """Extract YAML frontmatter fields from a markdown file's text."""
    m = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not m:
        return None
    fm = m.group(1)
    result = {}
    for field in ("title", "date", "slug", "url", "source", "source_name", "author"):
        match = re.search(rf'^{field}:\s*"?(.+?)"?\s*$', fm, re.MULTILINE)
        if match:
            result[field] = match.group(1).strip('"').strip("'")
    return result


def _build_knowledge_index(force: bool = False) -> list:
    """Scan knowledge article files, return list of frontmatter dicts.

    Caches to knowledge/_index.json. Uses cache if <1hr old unless force=True.
    """
    cache_file = _CFG["knowledge_dir"] / "_index.json"
    if not force and cache_file.exists():
        age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if age < 3600:
            try:
                with open(cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    print("  Building knowledge index...")
    kdir = _CFG["knowledge_dir"]
    if not kdir.exists():
        return []

    articles = []
    for source_dir in sorted(kdir.iterdir()):
        if not source_dir.is_dir() or source_dir.name.startswith("_"):
            continue
        for md_file in sorted(source_dir.glob("*.md")):
            try:
                # Read only the first 1KB for frontmatter
                with open(md_file, encoding="utf-8", errors="ignore") as f:
                    head = f.read(1024)
                fm = _parse_frontmatter(head)
                if fm:
                    fm["_file"] = md_file.name
                    fm["_source_key"] = source_dir.name
                    articles.append(fm)
            except OSError:
                continue

    # Sort by date descending
    articles.sort(key=lambda a: a.get("date", ""), reverse=True)

    # Cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(articles, f)
        print(f"  Cached {len(articles)} articles to {cache_file.name}")
    except OSError as e:
        print(f"  ! cache write failed: {e}")

    return articles


# ── Content & Loop helpers ───────────────────────────────────────────────────

def _list_content() -> list:
    """List content draft files."""
    cdir = _CFG["content_dir"]
    if not cdir or not cdir.exists():
        return []
    items = []
    for f in sorted(cdir.iterdir()):
        if f.is_file() and f.suffix in (".md", ".txt"):
            items.append({"filename": f.name, "size": f.stat().st_size,
                          "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    # Also check drafts subdirectory
    drafts_dir = _CFG.get("content_drafts_dir")
    if drafts_dir and drafts_dir.exists():
        for f in sorted(drafts_dir.iterdir()):
            if f.is_file() and f.suffix in (".md", ".txt"):
                items.append({"filename": f"drafts/{f.name}", "size": f.stat().st_size,
                              "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return items


def _read_content(filename: str) -> str | None:
    """Read a content file by name (supports drafts/ prefix)."""
    cdir = _CFG["content_dir"]
    if not cdir:
        return None
    path = cdir / filename
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    return None


def _list_loop_runs() -> list:
    """List loop run dates, newest first."""
    ldir = _CFG["loop_dir"]
    if not ldir or not ldir.exists():
        return []
    dates = []
    for d in sorted(ldir.iterdir(), reverse=True):
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name):
            dates.append(d.name)
    return dates


def _get_loop_run(date: str) -> dict | None:
    """Get summary + file list for a loop run."""
    run_dir = _CFG["loop_dir"] / date
    if not run_dir.exists():
        return None
    result = {"date": date, "files": []}
    summary_file = run_dir / "summary.json"
    if summary_file.exists():
        result["summary"] = _load_json(summary_file, {})
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.suffix in (".md", ".json"):
            result["files"].append(f.name)
    return result


def _read_loop_file(date: str, filename: str) -> str | None:
    """Read a specific file from a loop run."""
    path = _CFG["loop_dir"] / date / filename
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    return None


# ── HTTP Handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Quieter logging
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, status=200):
        self._text(text, status, "text/html; charset=utf-8")

    def _not_found(self):
        self._json({"error": "not found"}, 404)

    def _params(self) -> dict:
        parsed = urlparse(self.path)
        return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

    def _path_parts(self) -> list:
        parsed = urlparse(self.path)
        return [p for p in parsed.path.strip("/").split("/") if p]

    def do_GET(self):
        parts = self._path_parts()
        params = self._params()

        # Serve dashboard
        if not parts or (len(parts) == 1 and parts[0] in ("index.html", "dashboard")):
            if _DASHBOARD_HTML.exists():
                self._html(_DASHBOARD_HTML.read_text(encoding="utf-8"))
            else:
                self._text("dashboard not found", 404)
            return

        # API routing
        if parts[0] != "api":
            self._not_found()
            return

        route = "/".join(parts[1:])

        # /api/feed
        if route == "feed":
            items = _feed_items
            src = params.get("source", "")
            q = params.get("q", "").lower()
            if src:
                items = [i for i in items if i.get("source") == src]
            if q:
                items = [i for i in items if
                         q in (i.get("text") or i.get("content") or "").lower() or
                         q in (i.get("author") or "").lower() or
                         q in (i.get("display_name") or "").lower() or
                         q in (i.get("title") or "").lower()]
            limit = int(params.get("limit", 0))
            offset = int(params.get("offset", 0))
            total = len(items)
            if offset:
                items = items[offset:]
            if limit:
                items = items[:limit]
            self._json({"items": items, "total": total})
            return

        # /api/sources
        if route == "sources":
            self._json(_feed_sources)
            return

        # /api/knowledge/sources
        if route == "knowledge/sources":
            # Enrich with article counts
            counts = {}
            for a in _knowledge_index:
                k = a.get("_source_key", "")
                counts[k] = counts.get(k, 0) + 1
            enriched = [{**s, "article_count": counts.get(s["key"], 0)} for s in _knowledge_sources]
            self._json(enriched)
            return

        # /api/knowledge/articles
        if route == "knowledge/articles":
            items = _knowledge_index
            src = params.get("source", "")
            q = params.get("q", "").lower()
            since = params.get("since", "")
            if src:
                items = [a for a in items if a.get("_source_key") == src]
            if q:
                items = [a for a in items if
                         q in (a.get("title") or "").lower() or
                         q in (a.get("author") or "").lower() or
                         q in (a.get("source_name") or "").lower()]
            if since:
                items = [a for a in items if (a.get("date") or "") >= since]
            total = len(items)
            limit = int(params.get("limit", 50))
            offset = int(params.get("offset", 0))
            self._json({"articles": items[offset:offset + limit], "total": total})
            return

        # /api/knowledge/article/<source>/<filename>
        if len(parts) >= 4 and parts[1] == "knowledge" and parts[2] == "article":
            source_key = parts[3]
            filename = "/".join(parts[4:]) if len(parts) > 4 else ""
            if not filename:
                self._not_found()
                return
            path = _CFG["knowledge_dir"] / source_key / filename
            if not path.exists() or not path.is_file():
                self._not_found()
                return
            text = path.read_text(encoding="utf-8", errors="ignore")
            fm = _parse_frontmatter(text)
            # Extract body (everything after second ---)
            body_match = re.match(r"^---\n.+?\n---\n*", text, re.DOTALL)
            body = text[body_match.end():] if body_match else text
            self._json({"frontmatter": fm or {}, "content": body})
            return

        # /api/ideas
        if route == "ideas":
            items = _ideas
            status = params.get("status", "")
            pillar = params.get("pillar", "").lower()
            platform = params.get("platform", "").lower()
            q = params.get("q", "").lower()
            if status:
                items = [i for i in items if i.get("status") == status]
            if pillar:
                items = [i for i in items if pillar in (i.get("pillar") or "").lower()]
            if platform:
                items = [i for i in items if platform in (i.get("platform") or "").lower()]
            if q:
                items = [i for i in items if
                         q in (i.get("title") or "").lower() or
                         q in (i.get("description") or "").lower() or
                         q in (i.get("hook") or "").lower()]
            self._json(items)
            return

        # /api/content
        if route == "content":
            self._json(_list_content())
            return

        # /api/content/<filename>
        if len(parts) >= 3 and parts[1] == "content":
            filename = "/".join(parts[2:])
            text = _read_content(filename)
            if text is None:
                self._not_found()
                return
            self._text(text)
            return

        # /api/learnings
        if route == "learnings":
            lpath = _DATA_DIR / "learnings.md"
            if lpath.exists():
                self._text(lpath.read_text(encoding="utf-8", errors="ignore"))
            else:
                self._text("")
            return

        # /api/learn — list learn report files
        if route == "learn":
            learn_dir = _DATA_DIR / "learn"
            files = []
            if learn_dir.exists():
                for f in sorted(learn_dir.iterdir(), reverse=True):
                    if f.is_file() and f.suffix == ".md":
                        files.append({"filename": f.name,
                                      "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
            self._json(files)
            return

        # /api/learn/<filename>
        if len(parts) >= 3 and parts[1] == "learn":
            filename = parts[2]
            learn_dir = _DATA_DIR / "learn"
            path = learn_dir / filename
            if path.exists() and path.is_file():
                self._text(path.read_text(encoding="utf-8", errors="ignore"))
            else:
                self._not_found()
            return

        # /api/loop
        if route == "loop":
            self._json(_list_loop_runs())
            return

        # /api/loop/<date>
        if len(parts) == 3 and parts[1] == "loop":
            result = _get_loop_run(parts[2])
            if result is None:
                self._not_found()
                return
            self._json(result)
            return

        # /api/loop/<date>/<file>
        if len(parts) == 4 and parts[1] == "loop":
            text = _read_loop_file(parts[2], parts[3])
            if text is None:
                self._not_found()
                return
            self._text(text)
            return

        # /api/reload
        if route == "reload":
            _startup(rebuild_index=True)
            self._json({"status": "ok", "feed_items": len(_feed_items),
                         "knowledge_articles": len(_knowledge_index),
                         "ideas": len(_ideas)})
            return

        self._not_found()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parts = self._path_parts()
        if parts[0] != "api":
            self._not_found()
            return
        route = "/".join(parts[1:])

        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return

        if route == "sources/add":
            ok, msg = _sources_add(body)
            if ok:
                _load_feed()
            self._json({"ok": ok, "message": msg})
            return

        if route == "sources/remove":
            ok, msg = _sources_remove(body)
            if ok:
                _load_feed()
            self._json({"ok": ok, "message": msg})
            return

        if route == "knowledge/sources/add":
            ok, msg = _knowledge_add(body)
            if ok:
                _reload_knowledge()
            self._json({"ok": ok, "message": msg})
            return

        if route == "knowledge/sources/remove":
            ok, msg = _knowledge_remove(body)
            if ok:
                _reload_knowledge(rebuild_index=False)
            self._json({"ok": ok, "message": msg})
            return

        self._not_found()


# ── Startup ──────────────────────────────────────────────────────────────────

def _reload_knowledge(rebuild_index: bool = True):
    global _knowledge_sources, _knowledge_index
    _knowledge_sources = _parse_knowledge_index_md()
    _knowledge_index = _build_knowledge_index(force=rebuild_index)


def _startup(rebuild_index: bool = False):
    global _knowledge_sources, _knowledge_index
    print(f"  Data dir: {_DATA_DIR}")
    _load_feed()
    print(f"  Feed: {len(_feed_items)} items, {len(_feed_sources.get('feed_sources', []))} sources")
    _load_ideas()
    print(f"  Ideas: {len(_ideas)}")
    _knowledge_sources = _parse_knowledge_index_md()
    print(f"  Knowledge sources: {len(_knowledge_sources)}")
    _knowledge_index = _build_knowledge_index(force=rebuild_index)
    print(f"  Knowledge articles: {len(_knowledge_index)}")


def main():
    parser = argparse.ArgumentParser(description="Hum dashboard server")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--open", action="store_true", help="Open browser on start")
    parser.add_argument("--rebuild-index", action="store_true", help="Force rebuild knowledge index")
    args = parser.parse_args()

    print("hum dashboard")
    _startup(rebuild_index=args.rebuild_index)

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"\n  → {url}\n")

    if args.open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
