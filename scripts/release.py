"""Releases ClipLab. This is what Actions > Release > Run workflow runs (.github/workflows/release.yml).

    python scripts/release.py check      # say what a release would do, and change nothing
    python scripts/release.py publish    # release: move main, tag, publish

A release takes what's on dev:
  1. the tests workflow passed on dev's newest commit
  2. dev's __version__ isn't released yet (there's no tag v<version>), and changelog.json's newest entry is that
     version
  3. main fast-forwards to dev (it stops if main has commits dev doesn't)
  4. tag v<version> and a GitHub Release: notes from the changelog entry, cliplab-<version>.zip (the code that
     installs and updates use) and its .sha256
It needs git, and the GitHub CLI (gh) signed in or a GH_TOKEN. Nothing is released automatically.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY") or "ccarlson41/cliplab"
DIST = Path(__file__).resolve().parent.parent / "dist"
TESTS = ".github/workflows/tests.yml"
INSTALL = "irm https://raw.githubusercontent.com/ccarlson41/cliplab/main/install.ps1 | iex"


class Stop(Exception):
    """A reason not to release, said plainly."""


def read_version(init_text):
    found = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', init_text, re.M)
    if not found:
        raise Stop("cliplab/__init__.py on dev has no __version__ like \"1.2.3\".")
    return found.group(1)


def release_entry(changelog, version):
    """The What's new entry for `version`: it has to be the newest one in changelog.json."""
    newest = changelog[0] if isinstance(changelog, list) and changelog else {}
    if newest.get("version") != version:
        raise Stop(f"changelog.json's newest entry is {newest.get('version') or 'missing'}, but __version__ is "
                   f"{version}. Add {version}'s What's new entry at the top of changelog.json.")
    return newest


def render_notes(entry, sha256):
    """The release notes: the What's new highlights, how to install, and the zip's checksum."""
    lines = [f"## {entry['title']}", ""]
    for h in entry["highlights"]:
        stop = "" if re.search(r"[.!?]$", h["headline"]) else "."
        lines.append(f"- {h['icon']} **{h['headline']}{stop}** {h['text']}")
    v = entry["version"]
    lines += ["", "**New here?** Paste this into PowerShell:", "", "```powershell", INSTALL, "```", "",
              "**Already have ClipLab?** It updates itself (Settings > More > Updates), or run the line above again.",
              "", f"`cliplab-{v}.zip` SHA-256: `{sha256}`", ""]
    return "\n".join(lines)


def tests_verdict(runs, sha):
    """Did the tests workflow pass on commit sha? runs is GitHub's list of that workflow's runs. Raises Stop if not."""
    mine = [r for r in runs if r.get("head_sha") == sha]
    if any(r.get("conclusion") == "success" for r in mine):
        return next(r.get("html_url", "") for r in mine if r.get("conclusion") == "success")
    if any(r.get("status") != "completed" for r in mine):
        raise Stop(f"The tests are still running on dev ({sha[:7]}). Run Release again when they've finished.")
    if mine:
        raise Stop(f"The tests failed on dev ({sha[:7]}): {mine[0].get('html_url', '')}. Fix dev first.")
    raise Stop(f"The tests haven't run on dev's newest commit ({sha[:7]}). Push to dev or re-run them, then retry.")


def workflow_changes(paths):
    return [p for p in paths if p.startswith(".github/workflows/")]


def run(*cmd, quiet=False):
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if out.returncode and not quiet:
        raise Stop(f"`{' '.join(cmd)}` failed: {(out.stderr or out.stdout).strip()[:500]}")
    return out


def plan():
    """Everything a release checks, and what it would do. Raises Stop with the reason it can't go ahead."""
    run("git", "fetch", "--quiet", "--tags", "origin", "main", "dev")
    sha = run("git", "rev-parse", "origin/dev").stdout.strip()
    main_sha = run("git", "rev-parse", "origin/main").stdout.strip()
    version = read_version(run("git", "show", f"{sha}:cliplab/__init__.py").stdout)
    tag = f"v{version}"
    if run("git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}").stdout.strip():
        raise Stop(f"{tag} is already released. To release dev, bump __version__ there and give it a new What's new "
                   f"entry (see the README's Developing section).")
    entry = release_entry(json.loads(run("git", "show", f"{sha}:cliplab/changelog.json").stdout), version)
    runs = json.loads(run("gh", "api", f"repos/{REPO}/actions/workflows/{os.path.basename(TESTS)}/runs"
                                       f"?head_sha={sha}&per_page=50").stdout).get("workflow_runs", [])
    tests_url = tests_verdict(runs, sha)
    move = []
    if main_sha != sha:
        if run("git", "merge-base", "--is-ancestor", main_sha, sha, quiet=True).returncode:
            raise Stop("main has commits that dev doesn't, so it can't fast-forward. Merge main into dev (and get "
                       "that reviewed) first.")
        move = workflow_changes(run("git", "diff", "--name-only", main_sha, sha).stdout.split())
    return {"sha": sha, "main": main_sha, "version": version, "tag": tag, "entry": entry, "tests": tests_url,
            "workflow_changes": move}


def build(p):
    """cliplab-<version>.zip of dev's commit (every tracked file, in a cliplab-<version> folder), its .sha256 and the
    notes, in dist/."""
    DIST.mkdir(exist_ok=True)
    v = p["version"]
    zip_path = DIST / f"cliplab-{v}.zip"
    run("git", "archive", "--format=zip", f"--prefix=cliplab-{v}/", "-o", str(zip_path), p["sha"])
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (DIST / f"cliplab-{v}.zip.sha256").write_text(f"{digest}  cliplab-{v}.zip\n", encoding="utf-8", newline="\n")
    (DIST / "notes.md").write_text(render_notes(p["entry"], digest), encoding="utf-8", newline="\n")
    return zip_path, digest


def main(argv):
    mode = argv[0] if argv else "check"
    if mode not in ("check", "publish"):
        print(__doc__)
        return 2
    try:
        p = plan()
        zip_path, digest = build(p)
        print(f"dev {p['sha'][:7]} is ClipLab {p['version']}: \"{p['entry']['title']}\"")
        print(f"  tests passed: {p['tests']}")
        print(f"  {zip_path.name}  sha256 {digest}")
        print("  main is already at dev" if p["main"] == p["sha"] else f"  main {p['main'][:7]} fast-forwards to dev")
        if p["workflow_changes"] and os.environ.get("GITHUB_ACTIONS") == "true":
            raise Stop("This release changes " + ", ".join(p["workflow_changes"]) + ", and GitHub doesn't let a "
                       "workflow push changes to workflows. Move main yourself once (git fetch origin, then git push "
                       "origin origin/dev:main) and run Release again: it carries on from there.")
        if mode == "check":
            print(f"Ready to release {p['tag']}. Nothing was changed (this was a check).")
            return 0
        if p["main"] != p["sha"]:
            run("git", "push", "origin", f"{p['sha']}:refs/heads/main")
            print(f"main moved to {p['sha'][:7]}")
        run("gh", "release", "create", p["tag"], str(zip_path), f"{zip_path}.sha256", "--repo", REPO,
            "--target", p["sha"], "--title", f"ClipLab {p['version']}: {p['entry']['title']}",
            "--notes-file", str(DIST / "notes.md"))
        print(f"Released {p['tag']}: https://github.com/{REPO}/releases/tag/{p['tag']}")
        return 0
    except Stop as e:
        print(f"Not released: {e}", file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):   # show the reason on the workflow run's page too
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(f"### Not released\n\n{e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
