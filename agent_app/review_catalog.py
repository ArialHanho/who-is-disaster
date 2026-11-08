"""Complete branch inventory without checking out code or calling an AI."""
from .review_source import git, normalize_source


def branches(source):
    source, _ = normalize_source(source)
    rows, default = [], ""
    if source.startswith("https://"):
        output = git(".", "ls-remote", "--symref", "--", source + ".git", "HEAD", "refs/heads/*", timeout=90)
        for line in output.decode("utf-8", "replace").splitlines():
            value, _, ref = line.partition("\t")
            if value.startswith("ref: refs/heads/") and ref == "HEAD":
                default = value.removeprefix("ref: refs/heads/")
            elif ref.startswith("refs/heads/"):
                name = ref.removeprefix("refs/heads/")
                rows.append({"name": name, "head": value, "kind": "remote", "subject": "", "date": ""})
    else:
        default = git(source, "symbolic-ref", "--quiet", "--short", "HEAD", allowed_codes=(0, 1)).decode().strip()
        output = git(source, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(subject)%00%(committerdate:iso8601-strict)%00%(symref)", "refs/heads/", "refs/remotes/origin/")
        for line in output.decode("utf-8", "replace").splitlines():
            ref, head, subject, date, symbolic = line.split("\0")
            if symbolic:
                continue
            local = ref.startswith("refs/heads/")
            name = ref.removeprefix("refs/heads/" if local else "refs/remotes/")
            rows.append({"name": name, "head": head, "kind": "local" if local else "tracking", "subject": subject, "date": date})
    for row in rows:
        row["default"] = row["name"] == default
    return {"default": default, "branches": sorted(rows, key=lambda r: (not r["default"], r["name"].lower()))}
