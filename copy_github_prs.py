"""Copy GitHub pull requests between remotes in a local checkout.

The script expects a local clone that has both repositories configured as
Git remotes, for example:

    git -C ..\\IFC4.3.x-development\\ remote -v
    python copy_github_prs.py ..\\IFC4.3.x-development --source-remote own --target-remote origin

Provide a GitHub token when prompted:

    python copy_github_prs.py ..\\IFC4.3.x-development

Only branch content, PR title, and PR description are copied. Review comments,
issue comments, labels, assignees, reviewers, and other metadata are not copied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class CommandError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [
            f"Command failed with exit code {self.returncode}:",
            "  " + printable_cmd(self.cmd),
        ]
        if self.stdout.strip():
            lines.extend(["stdout:", self.stdout.rstrip()])
        if self.stderr.strip():
            lines.extend(["stderr:", self.stderr.rstrip()])
        return "\n".join(lines)


class Terminal:
    def close(self) -> None:
        return None

    def confirm(self, prompt: str) -> bool:
        while True:
            answer = input(f"{prompt} [y/N] ").strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            print("Please answer y or n.", file=sys.stderr)


class GitAskPass:
    """Temporary GIT_ASKPASS helper for HTTPS GitHub remotes."""

    def __init__(self, env: dict[str, str]):
        self.env = env
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> dict[str, str]:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="copy-github-prs-")
        root = Path(self._tmpdir.name)

        if os.name == "nt":
            helper = root / "git-askpass.cmd"
            helper.write_text(
                "@echo off\n"
                "echo %~1 | findstr /I \"Username\" >nul\n"
                "if %ERRORLEVEL%==0 (\n"
                "  echo x-access-token\n"
                ") else (\n"
                "  echo %GH_TOKEN%\n"
                ")\n",
                encoding="utf-8",
            )
        else:
            helper = root / "git-askpass.sh"
            helper.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf '%s\\n' x-access-token ;;\n"
                "  *) printf '%s\\n' \"$GH_TOKEN\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)

        self.env["GIT_ASKPASS"] = str(helper)
        self.env["GIT_TERMINAL_PROMPT"] = "0"
        return self.env

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


def printable_cmd(cmd: list[str]) -> str:
    return " ".join(quote_arg(part) for part in cmd)


def quote_arg(part: str) -> str:
    if re.search(r"\s", part):
        return '"' + part.replace('"', '\\"') + '"'
    return part


def run(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def run_json(cmd: list[str], cwd: Path, env: dict[str, str]) -> Any:
    proc = run(cmd, cwd, env)
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def read_token() -> str:
    token = input("GitHub token: ").strip()
    if not token:
        raise RuntimeError("No GitHub token was provided.")
    return token


def github_repo_from_remote_url(url: str) -> str:
    url = url.strip()

    scp_like = re.match(r"^(?:[^@]+@)?github\.com:(?P<path>.+)$", url)
    if scp_like:
        return normalize_repo_path(scp_like.group("path"))

    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        return normalize_repo_path(parsed.path)

    raise RuntimeError(f"Remote URL is not a github.com repository URL: {url}")


def normalize_repo_path(path: str) -> str:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise RuntimeError(f"Cannot infer owner/repo from remote path: {path}")
    return f"{parts[-2]}/{parts[-1]}"


def print_remote_summary(
    repo_path: Path,
    source_remote: str,
    target_remote: str,
    source_repo: str,
    target_repo: str,
    env: dict[str, str],
) -> None:
    remotes = run(["git", "remote", "-v"], repo_path, env).stdout.rstrip()
    print("Configured remotes:", file=sys.stderr)
    print(remotes, file=sys.stderr)
    print(file=sys.stderr)
    print(f"Source remote: {source_remote} -> {source_repo}", file=sys.stderr)
    print(f"Target remote: {target_remote} -> {target_repo}", file=sys.stderr)
    print(file=sys.stderr)


def ensure_tools_available() -> None:
    missing = [tool for tool in ("git", "gh") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError("Missing required command(s): " + ", ".join(missing))


def ensure_git_checkout(repo_path: Path, env: dict[str, str]) -> None:
    run(["git", "rev-parse", "--show-toplevel"], repo_path, env)


def get_remote_url(repo_path: Path, remote: str, env: dict[str, str]) -> str:
    return run(["git", "remote", "get-url", remote], repo_path, env).stdout.strip()


def list_source_prs(
    repo_path: Path,
    source_repo: str,
    limit: int,
    env: dict[str, str],
) -> list[dict[str, Any]]:
    fields = [
        "number",
        "title",
        "body",
        "baseRefName",
        "headRefName",
        "headRefOid",
        "reviewDecision",
        "url",
        "isDraft",
        "author",
    ]
    return run_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            source_repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            ",".join(fields),
        ],
        repo_path,
        env,
    )


def branch_exists(repo_path: Path, remote: str, branch: str, env: dict[str, str]) -> bool:
    proc = run(
        ["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        repo_path,
        env,
    )
    return bool(proc.stdout.strip())


def fetch_source_pr_head(
    repo_path: Path,
    source_remote: str,
    pr_number: int,
    env: dict[str, str],
) -> str:
    run(["git", "fetch", source_remote, f"refs/pull/{pr_number}/head"], repo_path, env)
    return run(["git", "rev-parse", "FETCH_HEAD"], repo_path, env).stdout.strip()


def push_target_branch(
    repo_path: Path,
    target_remote: str,
    commit: str,
    branch: str,
    env: dict[str, str],
) -> None:
    cmd = ["git", "push", target_remote, f"{commit}:refs/heads/{branch}"]
    run(cmd, repo_path, env)


def list_existing_target_prs(
    repo_path: Path,
    target_repo: str,
    branch: str,
    env: dict[str, str],
) -> list[dict[str, Any]]:
    return run_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            target_repo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,title,url",
        ],
        repo_path,
        env,
    )


def create_target_pr(
    repo_path: Path,
    target_repo: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    env: dict[str, str],
) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as body_file:
        body_file.write(body)
        body_path = Path(body_file.name)

    try:
        proc = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                target_repo,
                "--head",
                branch,
                "--base",
                base,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ],
            repo_path,
            env,
        )
        return proc.stdout.strip()
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass


def print_pr_summary(pr: dict[str, Any], target_branch: str) -> None:
    author = pr.get("author") or {}
    author_login = author.get("login") or "unknown"
    print("-" * 72, file=sys.stderr)
    print(f"Source PR: #{pr['number']} {pr.get('title') or ''}", file=sys.stderr)
    print(f"URL:       {pr.get('url') or ''}", file=sys.stderr)
    print(f"Author:    {author_login}", file=sys.stderr)
    print(f"Base:      {pr.get('baseRefName') or ''}", file=sys.stderr)
    print(f"Head:      {pr.get('headRefName') or ''}", file=sys.stderr)
    print(f"Commit:    {pr.get('headRefOid') or ''}", file=sys.stderr)
    print(f"Review:    {pr.get('reviewDecision') or 'NONE'}", file=sys.stderr)
    print(f"Draft:     {pr.get('isDraft')}", file=sys.stderr)
    print(f"Target:    {target_branch}", file=sys.stderr)


def process_pr(
    pr: dict[str, Any],
    repo_path: Path,
    source_remote: str,
    target_remote: str,
    target_repo: str,
    env: dict[str, str],
    terminal: Terminal,
) -> bool:
    pr_number = int(pr["number"])
    target_branch = pr.get("headRefName") or ""
    if not target_branch:
        raise RuntimeError(f"Source PR #{pr_number} has no head branch name.")
    print_pr_summary(pr, target_branch)

    if not terminal.confirm("Process this PR"):
        print("Skipped.", file=sys.stderr)
        return False

    base = pr.get("baseRefName")
    if not base:
        print("Skipped because the source PR has no base branch.", file=sys.stderr)
        return False

    if not branch_exists(repo_path, target_remote, base, env):
        print(
            f"Warning: target base branch {base!r} was not found on {target_remote}.",
            file=sys.stderr,
        )
        if not terminal.confirm("Continue anyway"):
            print("Skipped.", file=sys.stderr)
            return False

    if branch_exists(repo_path, target_remote, target_branch, env):
        raise RuntimeError(
            f"Target branch {target_branch!r} already exists on {target_remote}."
        )

    print(
        f"Will fetch source PR head with: git fetch {source_remote} "
        f"refs/pull/{pr_number}/head",
        file=sys.stderr,
    )
    if not terminal.confirm("Fetch this source PR head"):
        print("Skipped.", file=sys.stderr)
        return False

    commit = fetch_source_pr_head(repo_path, source_remote, pr_number, env)
    expected_commit = pr.get("headRefOid")
    if expected_commit and commit != expected_commit:
        print(
            f"Warning: fetched commit {commit} differs from listed head {expected_commit}.",
            file=sys.stderr,
        )
        if not terminal.confirm("Continue with the fetched commit"):
            print("Skipped.", file=sys.stderr)
            return False

    push_cmd = ["git", "push", target_remote, f"{commit}:refs/heads/{target_branch}"]
    print("Will push with: " + printable_cmd(push_cmd), file=sys.stderr)
    if not terminal.confirm("Push this branch to the target remote"):
        print("Skipped before push.", file=sys.stderr)
        return False

    push_target_branch(
        repo_path,
        target_remote,
        commit,
        target_branch,
        env,
    )

    existing_prs = list_existing_target_prs(repo_path, target_repo, target_branch, env)
    if existing_prs:
        print("An open target PR already exists for this branch:", file=sys.stderr)
        for existing in existing_prs:
            print(
                f"  #{existing['number']} {existing.get('title') or ''} "
                f"{existing.get('url') or ''}",
                file=sys.stderr,
            )
        print("Branch was pushed; target PR creation skipped.", file=sys.stderr)
        return True

    title = pr.get("title") or f"Copy of PR #{pr_number}"
    body = pr.get("body") or ""
    print(
        "Will create target PR with: "
        + printable_cmd(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                target_repo,
                "--head",
                target_branch,
                "--base",
                base,
                "--title",
                title,
                "--body-file",
                "<temp-file>",
            ]
        ),
        file=sys.stderr,
    )
    if not terminal.confirm("Create the target PR with copied title and description"):
        print("Branch was pushed; target PR creation skipped.", file=sys.stderr)
        return True

    created = create_target_pr(
        repo_path,
        target_repo,
        target_branch,
        base,
        title,
        body,
        env,
    )
    print(f"Created target PR: {created}", file=sys.stderr)
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy open GitHub PRs from one remote repository to another using "
            "a local checkout."
        )
    )
    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to the local clone that has both source and target remotes.",
    )
    parser.add_argument(
        "--source-remote",
        default="own",
        help="Remote name for the source GitHub repository. Default: own.",
    )
    parser.add_argument(
        "--target-remote",
        default="origin",
        help="Remote name for the target GitHub repository. Default: origin.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of open source PRs to inspect. Default: 100.",
    )

    approval_group = parser.add_mutually_exclusive_group()
    approval_group.add_argument(
        "--approved-only",
        dest="approved_only",
        action="store_true",
        default=True,
        help="Only process source PRs whose review decision is APPROVED. Default.",
    )
    approval_group.add_argument(
        "--include-unapproved",
        dest="approved_only",
        action="store_false",
        help="Also offer to process PRs that are not approved.",
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_path = args.repo_path.resolve()

    if not repo_path.exists():
        raise RuntimeError(f"Repository path does not exist: {repo_path}")

    ensure_tools_available()
    token = read_token()

    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token

    terminal = Terminal()
    try:
        with GitAskPass(env) as auth_env:
            ensure_git_checkout(repo_path, auth_env)

            source_url = get_remote_url(repo_path, args.source_remote, auth_env)
            target_url = get_remote_url(repo_path, args.target_remote, auth_env)
            source_repo = github_repo_from_remote_url(source_url)
            target_repo = github_repo_from_remote_url(target_url)

            if source_repo == target_repo:
                raise RuntimeError(
                    "Source and target remotes resolve to the same GitHub repository."
                )

            print_remote_summary(
                repo_path,
                args.source_remote,
                args.target_remote,
                source_repo,
                target_repo,
                auth_env,
            )
            if not terminal.confirm("Use these source and target repositories"):
                print("Aborted.", file=sys.stderr)
                return 1

            print(
                f"Will inspect up to {args.limit} open PRs from {source_repo}.",
                file=sys.stderr,
            )
            if args.approved_only:
                print("Filter: approved PRs only.", file=sys.stderr)
            else:
                print("Filter: approved and unapproved PRs.", file=sys.stderr)
            if not terminal.confirm("List source pull requests now"):
                print("Aborted.", file=sys.stderr)
                return 1

            prs = list_source_prs(repo_path, source_repo, args.limit, auth_env)
            if args.approved_only:
                skipped = [
                    pr for pr in prs if pr.get("reviewDecision") != "APPROVED"
                ]
                prs = [pr for pr in prs if pr.get("reviewDecision") == "APPROVED"]
                print(
                    f"Found {len(prs)} approved open PR(s); "
                    f"skipped {len(skipped)} not approved.",
                    file=sys.stderr,
                )
            else:
                print(f"Found {len(prs)} open PR(s).", file=sys.stderr)

            processed = 0
            for pr in prs:
                if process_pr(
                    pr,
                    repo_path,
                    args.source_remote,
                    args.target_remote,
                    target_repo,
                    auth_env,
                    terminal,
                ):
                    processed += 1

            print("-" * 72, file=sys.stderr)
            print(f"Done. Processed {processed} PR(s).", file=sys.stderr)
            return 0
    finally:
        terminal.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
