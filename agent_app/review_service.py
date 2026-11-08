"""Persistent review state, deduplication, and opt-in in-app polling."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from .review_source import GitSource, normalize_source, valid_branch
from .review_analysis import VERSION, analyze, fingerprint, finding_policy
from .review_parallel import parse_targets, capture, compose, sources_metadata
from .review_followup import verify, tracking, check_result, recover_integrated_views
from .review_catalog import branches
from . import github_account
from .github_actions import collect as collect_actions, failed_runs
from .review_authors import AuthorLookup


class ReviewService:
    def __init__(self, data_dir, analyzer=analyze, actions_collector=collect_actions):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "code-review.sqlite3"
        self.source = GitSource(self.root / "git-cache")
        self.authors = AuthorLookup(self.source.cache, self.root / 'authors')
        self.analyzer = analyzer
        self.actions_collector = actions_collector
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL, branch TEXT NOT NULL,
                rules TEXT NOT NULL DEFAULT '', auto INTEGER NOT NULL DEFAULT 0, interval INTEGER NOT NULL DEFAULT 30,
                last_head TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '', checked REAL NOT NULL DEFAULT 0,
                next_due REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', alerted_error TEXT NOT NULL DEFAULT '',
                UNIQUE(source, branch));
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id), head TEXT NOT NULL,
                stamp REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id), fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', disposition TEXT NOT NULL DEFAULT 'open',
                notified INTEGER NOT NULL DEFAULT 0, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                UNIQUE(repo_id, fingerprint));
            CREATE TABLE IF NOT EXISTS catalogs (repo_id INTEGER PRIMARY KEY REFERENCES repos(id), checked REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ci_reports (repo_id INTEGER PRIMARY KEY REFERENCES repos(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ci_notices (repo_id INTEGER NOT NULL REFERENCES repos(id), run_id INTEGER NOT NULL,
                attempt INTEGER NOT NULL, notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(repo_id,run_id,attempt));
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(repos)")}
            for name, definition in (("targets", "TEXT NOT NULL DEFAULT '[]'"), ("include_working", "INTEGER NOT NULL DEFAULT 0"), ("all_branches", "INTEGER NOT NULL DEFAULT 1"), ("open_prs", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE repos ADD COLUMN {name} {definition}")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def repos(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM repos ORDER BY id")]

    def repo(self, repo_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
            if not row:
                raise ValueError("연결된 저장소가 없어요.")
            return dict(row)

    def save_repo(self, source, branch="", rules="", auto=False, interval=30, repo_id=None, *, targets=None, include_working=False, all_branches=False, open_prs=False):
        source, name = normalize_source(source)
        branch = valid_branch(branch)
        targets = json.dumps(parse_targets(targets, source), ensure_ascii=False)
        if include_working and source.startswith("https://"):
            raise ValueError("미커밋 변경은 로컬 Git 폴더를 연결해야 읽을 수 있어요.")
        rules = rules.strip()[:6000]
        interval = max(5, min(1440, int(interval)))
        with self.db() as db:
            if repo_id:
                # Source is immutable on edit so review evidence cannot move to another repository.
                original = db.execute("SELECT source,branch FROM repos WHERE id=?", (repo_id,)).fetchone()
                if not original or original["source"] != source or original["branch"] != branch:
                    raise ValueError("주소나 브랜치가 다르면 새 저장소로 연결해 주세요.")
                db.execute("UPDATE repos SET rules=?,auto=?,interval=?,next_due=? WHERE id=?", (rules, int(auto), interval, time.time()+interval*60, repo_id))
            else:
                try:
                    repo_id = db.execute("INSERT INTO repos(source,name,branch,rules,auto,interval,next_due) VALUES(?,?,?,?,?,?,?)",
                                         (source, name, branch, rules, int(auto), interval, time.time()+interval*60)).lastrowid
                except sqlite3.IntegrityError:
                    raise ValueError("이미 연결한 저장소와 브랜치예요.") from None
            db.execute("UPDATE repos SET targets=?,include_working=?,all_branches=?,open_prs=? WHERE id=?", (targets, int(include_working), int(all_branches), int(open_prs), repo_id))
        return repo_id

    def connect_github(self, selected, interval=5):
        sources = list(dict.fromkeys(normalize_source(r["source"])[0] for r in selected))
        if not sources or any(not s.startswith("https://github.com/") for s in sources):
            raise ValueError("GitHub 저장소를 선택해 주세요.")
        connected, interval = [], max(5, min(1440, int(interval)))
        for source in sources:
            repo = next((r for r in self.repos() if r["source"].casefold() == source.casefold()), None)
            rid = repo["id"] if repo else self.save_repo(source, auto=True, interval=interval, all_branches=True, open_prs=True)
            with self.db() as db:
                db.execute("UPDATE repos SET auto=1,all_branches=1,open_prs=1,interval=?,next_due=0 WHERE id=?", (interval, rid))
            connected.append(rid)
        return connected

    def catalog(self, repo_id, refresh=False, *, include_pulls=None):
        if refresh:
            repo = self.repo(repo_id)
            data = branches(repo["source"])
            data["pulls"] = github_account.open_pulls(repo["source"]) if (repo["open_prs"] if include_pulls is None else include_pulls) else []
            with self.db() as db:
                db.execute("INSERT INTO catalogs VALUES(?,?,?) ON CONFLICT(repo_id) DO UPDATE SET checked=excluded.checked,payload=excluded.payload",
                           (repo_id, time.time(), json.dumps(data, ensure_ascii=False)))
        with self.db() as db:
            row = db.execute("SELECT * FROM catalogs WHERE repo_id=?", (repo_id,)).fetchone()
            return {**json.loads(row["payload"]), "checked": row["checked"]} if row else {"branches": [], "default": "", "checked": 0}

    def set_scope(self, repo_id, all_branches, targets, open_prs=None):
        repo = self.repo(repo_id)
        selected = parse_targets(targets, repo["source"])
        with self.db() as db:
            db.execute("UPDATE repos SET all_branches=?,targets=? WHERE id=?", (int(all_branches), json.dumps(selected), repo_id))
            if open_prs is not None:
                db.execute("UPDATE repos SET open_prs=? WHERE id=?", (int(open_prs), repo_id))

    def due(self):
        with self.db() as db:
            row = db.execute("SELECT id FROM repos WHERE auto=1 AND next_due<=? ORDER BY next_due LIMIT 1", (time.time(),)).fetchone()
            return row["id"] if row else None

    def due_repos(self):
        with self.db() as db:
            return [r["id"] for r in db.execute("SELECT id FROM repos WHERE auto=1 AND next_due<=? ORDER BY next_due,id", (time.time(),))]

    def disconnect(self, repo_id):
        # Remove only this app's connection/history. Never run a Git delete command.
        with self.db() as db:
            for table in ("findings", "runs", "catalogs", "ci_reports", "ci_notices"):
                db.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
            db.execute("DELETE FROM repos WHERE id=?", (repo_id,))

    def set_error(self, repo_id, message):
        with self.db() as db:
            db.execute("UPDATE repos SET error=?,checked=? WHERE id=?", (message, time.time(), repo_id))

    def actions_report(self, repo_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM ci_reports WHERE repo_id=?", (repo_id,)).fetchone()
            return json.loads(row[0]) if row else {}

    def refresh_actions(self, repo_id, targets=None, *, force=False, progress=None):
        repo, previous = self.repo(repo_id), self.actions_report(repo_id)
        targets = previous.get("targets", []) if targets is None else targets
        if not targets:
            return {"repo_id": repo_id, "ci_only": True, "ci_empty": True}
        options = {"force": force}
        if progress: options["progress"] = progress
        report = self.actions_collector(repo["source"], targets, previous, **options)
        with self.db() as db:
            db.execute("INSERT INTO ci_reports VALUES(?,?) ON CONFLICT(repo_id) DO UPDATE SET payload=excluded.payload",
                       (repo_id, json.dumps(report, ensure_ascii=False)))
            for run in failed_runs(report):
                db.execute("INSERT OR IGNORE INTO ci_notices(repo_id,run_id,attempt) VALUES(?,?,?)", (repo_id, run["id"], run["attempt"]))
            db.execute("DELETE FROM ci_notices WHERE repo_id=? AND run_id NOT IN (SELECT run_id FROM ci_notices WHERE repo_id=? ORDER BY run_id DESC LIMIT 500)", (repo_id, repo_id))
        return {"repo_id": repo_id, "ci_only": True, "ci_error": report.get("error", "")}

    def mark_read(self, finding_id):
        with self.db() as db:
            db.execute("UPDATE findings SET notified=1 WHERE id=?", (finding_id,))

    def mark_ci_read(self, repo_id, run):
        with self.db() as db:
            db.execute("UPDATE ci_notices SET notified=1 WHERE repo_id=? AND run_id=? AND attempt=?", (repo_id, run["id"], run["attempt"]))

    def review(self, repo_id, progress=None, *, force=False, all_scope=False, recheck_ids=None):
        repo = self.repo(repo_id)
        recheck_only = recheck_ids is not None
        if recheck_only: force = True
        if all_scope:
            repo = {**repo, "all_branches": 1, "open_prs": int(repo["source"].startswith("https://github.com/"))}
        with self.db() as db:
            # Advance before I/O; restarts and failures do not create a tight retry loop.
            db.execute("UPDATE repos SET next_due=? WHERE id=?", (time.time()+repo["interval"]*60, repo_id))
        try:
            if progress: progress("searching", "브랜치와 열린 PR을 가져오고 있어요")
            targets = json.loads(repo["targets"])
            catalog = self.catalog(repo_id, refresh=True, include_pulls=bool(repo["open_prs"])) if repo["all_branches"] or repo["open_prs"] else {}
            if repo["all_branches"]:
                targets = list(dict.fromkeys([r["name"] for r in catalog["branches"] if r["name"] != (repo["branch"] or catalog["default"])] + [t for t in targets if t.startswith("pr:")]))
            pulls = catalog.get("pulls", [])
            if repo["open_prs"]:
                targets = [t for t in targets if not t.startswith("pr:")] + [f"pr:{p['number']}" for p in pulls]
            parallel = bool(targets or repo["include_working"])
            if parallel:
                captured = capture(self.source, repo["source"], repo["branch"], targets, bool(repo["include_working"]))
                captured_heads = {v["id"]: v["head"] for v in captured["views"]}
                if any(captured_heads.get(f"pr:{p['number']}") != p["head"] for p in pulls):
                    raise ValueError("가져오는 동안 PR이 변경됐어요. 다음 확인 때 최신 상태로 다시 분석해요.")
                snapshot, revision = captured["base"], captured["signature"]
            else:
                snapshot = self.source.prepare(repo["source"], repo["branch"])
                revision = snapshot["head"]
            pull_revisions = sorted((p['number'], p['head'], p['base']) for p in pulls)
            signature = hashlib.sha256(json.dumps([revision, pull_revisions, repo["rules"], os.getenv("CODEX_MODEL", ""), VERSION], sort_keys=True).encode()).hexdigest()
            selection = {"all_branches": bool(repo["all_branches"]), "open_prs": bool(repo["open_prs"]),
                         "branches": len(catalog.get("branches", [])), "pulls": len(pulls), "one_time_all": all_scope}
            if signature == repo["signature"] and not force:
                with self.db() as db:
                    db.execute("UPDATE repos SET checked=?,error='',alerted_error='' WHERE id=?", (time.time(), repo_id))
                return {"repo_id": repo_id, "cached": True, "head": snapshot["head"], "selection": selection}
            if progress: progress("searching", "변경 내용을 모아 함께 살펴보고 있어요")
            snapshot = compose(self.source, captured) if parallel else self.source.read(snapshot, repo["last_head"])
            snapshot["pull_requests"] = pulls
            for pull in pulls:
                view = snapshot.get("views", {}).get(f"pr:{pull['number']}")
                if view:
                    view["base_branch"] = pull["base"]
                    view["label"] = f"PR #{pull['number']} → {pull['base']}"
            snapshot["initial_review"] = not bool(repo["last_head"])
            snapshot['project_rules'] = repo['rules']
            file_hashes = {}
            if parallel:
                for key, view in {"combined": snapshot, **snapshot["views"]}.items():
                    for path, body in view["files"].items():
                        file_hashes[key + "\0" + path] = hashlib.sha256(body.encode()).hexdigest()
                previous_runs = [r for r in self.runs(repo_id) if not r.get("ai_error") and not r.get('recheck_only')]
                previous = previous_runs[0].get("file_hashes", {}) if previous_runs else {}
                if not previous:
                    snapshot["initial_review"] = True
                snapshot["new_changed"] = [key for key, digest in file_hashes.items() if previous.get(key) != digest]
            if not snapshot["files"] and not snapshot.get("views"):
                raise ValueError("분석할 소스 파일이 없어요. 지원하는 코드가 커밋된 저장소를 연결해 주세요.")
            accepted = [f for f in self.findings(repo_id, True) if f["disposition"] == "accepted"]
            rules = repo["rules"]
            if accepted:
                rules += "\n사용자가 유지하기로 한 설계(동일 사유를 다시 경고하지 않음):\n" + "\n".join(
                    f["title"] + ": " + f["detail"][:400] for f in accepted[:12])
            if progress: progress("thinking", "수정된 코드를 비교하고 있어요" if recheck_only else "과한 설계·순환·변경 누락을 분석하고 있어요")
            result = ({'findings': [], 'summary': '선택한 항목의 수정 여부를 현재 코드로 확인했어요.', 'ai_error': '', 'recheck_only': True}
                      if recheck_only else self.analyzer(snapshot, rules, **({'progress':progress, 'cache_dir':None if force else self.root/'review-cache'} if self.analyzer is analyze else {})))
            result.update({"repo_id": repo_id, "head": snapshot["head"], "base": snapshot["base"], "web": snapshot["web"], "cached": False})
            result["selection"] = selection
            result["pull_requests"] = pulls
            if parallel:
                result.update(sources=sources_metadata(snapshot), snapshot_id=snapshot["snapshot_id"],
                              composition_complete=snapshot["composition_complete"], independent_views=snapshot.get("independent_views", []), file_hashes=file_hashes)
            previous = self.findings(repo_id, True, limit=None)
            if recheck_only:
                previous = [f for f in previous if f['id'] in set(recheck_ids) and f['disposition'] == 'open']
            if previous:
                # Preserve a path-identified Git conflict when previously missing
                # source evidence becomes available, rather than creating a duplicate.
                conflicts = {f.get("key"): f["fingerprint"] for f in previous if f.get("origin") == "git-merge-tree"}
                for finding in result["findings"]:
                    if finding.get("origin") == "git-merge-tree" and finding.get("key") in conflicts:
                        finding["fingerprint"] = conflicts[finding["key"]]
                recover_integrated_views(snapshot, previous)
                if progress: progress("thinking", "이전에 발견한 문제가 고쳐졌는지 비교하고 있어요")
                checks, error = verify(snapshot, previous, result["findings"], ai_error=result.get("ai_error", ""), force=force)
                result["followup"] = checks
                result["followup_error"] = error
            if progress: progress("organizing", "근거와 수정 지시문을 정리하고 있어요")
            self.record(repo, snapshot, signature, result)
            return result
        except Exception as exc:
            self.set_error(repo_id, str(exc))
            raise

    def record(self, repo, snapshot, signature, result):
        stamp = time.time()
        error = result.get("ai_error") or result.get("followup_error", "")
        with self.db() as db:
            previous = {r["fingerprint"]: dict(r) for r in db.execute("SELECT * FROM findings WHERE repo_id=?", (repo["id"],))}
            detected = {f.get("fingerprint") or fingerprint(f): f for f in result["findings"]}
            checks = result.get("followup", {})
            counts = {"open": 0, "resolved": 0, "uncertain": 0, "reopened": 0}
            for key in dict.fromkeys([*previous, *detected]):
                row = previous.get(key)
                if result.get('recheck_only') and row and row['id'] not in checks and str(row['id']) not in checks:
                    continue
                old = json.loads(row["payload"]) if row else {}
                current = detected.get(key)
                checked = checks.get(row["id"], checks.get(str(row["id"]))) if row else None
                if current:
                    finding = {**current, "head": snapshot["head"], "web": snapshot["web"],
                               "sources": sources_metadata(snapshot), "snapshot_id": snapshot.get("snapshot_id", "")}
                    state = "open"
                    checked = check_result(snapshot, state, "최신 코드에서 발견한 근거예요.", "detected", finding["evidence"])
                else:
                    finding = dict(old)
                    if old["category"] == "feature":
                        state = "not_seen" if not error else row["state"]
                    elif row["disposition"] != "open":
                        state = row["state"]
                    else:
                        checked = checked or check_result(snapshot, "uncertain", "이번 분석에 언급되지 않았지만 수정 근거는 아직 확인하지 못했어요.", "pending")
                        state = checked["state"]
                if checked and finding["category"] != "feature":
                    checked["previous_head"] = old.get("head", snapshot["head"])
                    checked["previous_evidence"] = old.get("evidence", finding["evidence"])
                    finding["verification"] = checked
                    counts[state] += 1
                confirmed = next((event["state"] for event in reversed(old.get("events", [])) if event["state"] in {"open", "resolved"}), row["state"] if row else "")
                reopened = bool(row and confirmed == "resolved" and state == "open")
                finding["reopened_count"] = old.get("reopened_count", 0) + int(reopened)
                counts["reopened"] += int(reopened)
                finding["original"] = old.get("original") or {"head": finding["head"], "evidence": finding["evidence"], "sources": finding.get("sources", {})}
                # Keep the first verified fix, even if later checks see an unrelated HEAD.
                finding["resolutions"] = list(old.get("resolutions", []))
                if checked and state == "resolved":
                    prior_resolution = old.get('resolution')
                    if not prior_resolution and confirmed == 'resolved' and finding['resolutions']:
                        prior_resolution = finding['resolutions'][-1]
                    if not prior_resolution or confirmed != 'resolved':
                        finding["resolution"] = {
                            "before": {k: old.get(k, finding.get(k)) for k in ("head", "evidence", "sources", "snapshot_id")},
                            "after": dict(checked)}
                        finding["resolutions"].append(finding["resolution"])
                        finding["resolutions"] = finding["resolutions"][-10:]
                    else:
                        finding["resolution"] = prior_resolution
                else:
                    finding.pop("resolution", None)
                if checked and not current and state == "open" and checked.get("evidence"):
                    finding.update(evidence=checked["evidence"], head=snapshot["head"], sources=checked["sources"], snapshot_id=snapshot.get("snapshot_id", ""))
                finding["tracking"] = tracking(snapshot, finding) if current or state in {"open", "resolved"} else old.get("tracking", {})
                events = list(old.get("events", []))
                if not row or state != row["state"]:
                    events.append({"state": state, "stamp": stamp, "head": snapshot["head"], "reason": (checked or {}).get("reason", ""),
                                   "method": (checked or {}).get("method", ""), "evidence": (checked or {}).get("evidence", []),
                                   "sources": (checked or {}).get("sources", {}), "snapshot_id": snapshot.get("snapshot_id", "")})
                finding["events"] = events[-20:]
                payload = json.dumps(finding, ensure_ascii=False)
                if row:
                    db.execute("UPDATE findings SET payload=?,state=?,last_seen=?,notified=? WHERE id=?",
                               (payload, state, stamp if current else row["last_seen"], 0 if reopened and row["disposition"] == "open" else row["notified"], row["id"]))
                else:
                    db.execute("INSERT INTO findings(repo_id,fingerprint,payload,state,first_seen,last_seen) VALUES(?,?,?,?,?,?)",
                               (repo["id"], key, payload, state, stamp, stamp))
            result["followup_counts"] = counts
            db.execute("INSERT INTO runs(repo_id,head,stamp,payload) VALUES(?,?,?,?)",
                       (repo["id"], snapshot["head"], stamp, json.dumps(result, ensure_ascii=False)))
            # Failed AI reviews stay retryable and keep the last successful comparison baseline.
            db.execute("UPDATE repos SET checked=?,error=?,last_head=?,signature=?,alerted_error=CASE WHEN ?='' THEN '' ELSE alerted_error END WHERE id=?",
                       (stamp, error, snapshot["head"] if not error and not result.get('recheck_only') else repo["last_head"],
                        signature if not error and not result.get('recheck_only') else repo["signature"], error, repo["id"]))
            db.execute("DELETE FROM runs WHERE repo_id=? AND id NOT IN (SELECT id FROM runs WHERE repo_id=? ORDER BY id DESC LIMIT 60)", (repo["id"], repo["id"]))

    def findings(self, repo_id, history=False, *, limit=80):
        with self.db() as db:
            query = "SELECT * FROM findings WHERE repo_id=?"
            if not history:
                query += " AND (state IN ('open','uncertain') OR (state='not_seen' AND json_extract(payload,'$.category')!='feature')) AND disposition='open'"
            query += " ORDER BY last_seen DESC,id DESC"
            if limit is not None:
                query += " LIMIT " + str(max(1, int(limit)))
            return [finding_policy({**json.loads(r["payload"]), **{k: r[k] for k in ("id", "repo_id", "fingerprint", "state", "disposition", "notified", "first_seen", "last_seen")}})
                    for r in db.execute(query, (repo_id,))]

    def runs(self, repo_id):
        with self.db() as db:
            return [{**json.loads(r["payload"]), "stamp": r["stamp"]} for r in db.execute("SELECT * FROM runs WHERE repo_id=? ORDER BY id DESC LIMIT 20", (repo_id,))]

    def latest_coverage(self, repo_id):
        with self.db() as db:
            row = db.execute("SELECT json_extract(payload,'$.coverage') FROM runs WHERE repo_id=? AND json_type(payload,'$.coverage')='object' ORDER BY id DESC LIMIT 1", (repo_id,)).fetchone()
            return json.loads(row[0]) if row else {}

    def disposition(self, finding_id, value):
        if value not in {"open", "accepted", "dismissed"}:
            raise ValueError("기록 상태가 올바르지 않아요.")
        with self.db() as db:
            db.execute("UPDATE findings SET disposition=?,notified=1 WHERE id=?", (value, finding_id))

    def pending_notice(self, repo_id):
        repo = self.repo(repo_id)
        findings = [f for f in self.findings(repo_id) if f["state"] == "open" and not f["notified"] and f["confidence"] == "high" and
                    f["category"] != "feature" and f["severity"] in {"problem", "warning"}]
        error = repo["error"] if repo["error"] != repo["alerted_error"] else ""
        with self.db() as db:
            unread = {(r[0], r[1]) for r in db.execute("SELECT run_id,attempt FROM ci_notices WHERE repo_id=? AND notified=0", (repo_id,))}
        report = self.actions_report(repo_id)
        ci = [r for r in failed_runs(report) if (r["id"], r["attempt"]) in unread] if not report.get("error") else []
        return {"repo": repo, "findings": findings, "error": error, "ci": ci}

    def mark_notified(self, notice):
        with self.db() as db:
            for f in notice["findings"]:
                db.execute("UPDATE findings SET notified=1 WHERE id=?", (f["id"],))
            for r in notice.get("ci", []):
                db.execute("UPDATE ci_notices SET notified=1 WHERE repo_id=? AND run_id=? AND attempt=?", (notice["repo"]["id"], r["id"], r["attempt"]))
            if notice["error"]:
                db.execute("UPDATE repos SET alerted_error=? WHERE id=?", (notice["error"], notice["repo"]["id"]))
