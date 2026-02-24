"""Helpers for interacting with Git repositories and remote scenario sources."""

import base64
import logging
import os
import urllib.parse

from click import UsageError
import requests
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from broker import exceptions, helpers
from broker.settings import settings

logger = logging.getLogger(__name__)
yaml = YAML(typ="safe")

# HTTP status code constants
HTTP_NOT_FOUND = 404
HTTP_UNAUTHORIZED = 401

# Minimum parts required for parsing repo paths
MIN_REPO_PARTS = 2
MIN_GITLAB_PARTS = 3
# Minimum parts required for web UI paths (blob/tree + ref + at least one path component)
MIN_WEB_UI_PARTS = 2  # or 3 for GitLab with the "-" separator


def _get_git_hosts():
    """Return configured scenario import hosts, supporting Dynaconf key normalization."""
    scenario_import = settings.get("SCENARIO_IMPORT", {}) or {}
    if not isinstance(scenario_import, dict):
        return []

    hosts = scenario_import.get("git_hosts")
    if hosts is None:
        hosts = scenario_import.get("GIT_HOSTS", [])

    return hosts if isinstance(hosts, list) else []


def _get_token(url):
    """Resolve token for a given Git URL from settings or environment variables."""
    # Check configured hosts first
    git_hosts = _get_git_hosts()
    for host in git_hosts:
        if url.startswith(host.get("url", "")):
            if token := host.get("token"):
                return token

    # Fallback to environment variables
    if "github" in url:
        return os.environ.get("GITHUB_TOKEN")
    if "gitlab" in url:
        return os.environ.get("GITLAB_TOKEN")
    return None


def _auto_detect_type(url):
    """Detect the Git host type (github/gitlab) from the URL."""
    if "github" in url:
        return "github"
    if "gitlab" in url:
        return "gitlab"

    # Check configured hosts
    git_hosts = _get_git_hosts()
    for host in git_hosts:
        if url.startswith(host.get("url", "")):
            if type_ := host.get("type"):
                return type_

    raise exceptions.ConfigurationError(
        f"Could not detect Git host type for {url}. "
        "Please configure this host in broker_settings.yaml under "
        "SCENARIO_IMPORT.git_hosts (or SCENARIO_IMPORT.GIT_HOSTS) "
        "with a 'type' field (github or gitlab)."
    )


class BaseAdapter:
    """Base class for repository adapters."""

    def list_remote_scenarios(self, path="", ref="master"):
        """List scenarios in the repository."""
        raise NotImplementedError

    def get_file_content(self, path, ref="master"):
        """Get the content of a file."""
        raise NotImplementedError

    def get_metadata(self, ref="master"):
        """Fetch metadata.yaml if it exists."""
        try:
            content = self.get_file_content("metadata.yaml", ref)
            return yaml.load(content)
        except (exceptions.BrokerError, KeyError, ValueError, YAMLError):
            return None

    def resolve_commit(self, ref="master"):
        """Resolve a ref to a full commit SHA."""
        raise NotImplementedError


class GitHubAdapter(BaseAdapter):
    """Adapter for GitHub repositories."""

    def __init__(self, owner, repo, base_url="https://api.github.com"):
        self.owner = owner
        self.repo = repo
        self.base_url = base_url
        self.repo_url = f"https://github.com/{owner}/{repo}"

    def _request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}{endpoint}"
        headers = kwargs.pop("headers", {})
        if token := _get_token(self.repo_url):
            headers["Authorization"] = f"token {token}"

        def prompt_func():
            return requests.request(method, url, headers=headers, timeout=30, **kwargs)

        try:
            response = helpers.simple_retry(prompt_func, (), {}, max_timeout=30)
            response.raise_for_status()
            return response
        except requests.HTTPError as e:
            if e.response.status_code == HTTP_NOT_FOUND:
                raise exceptions.BrokerError(
                    f"Resource not found: {url} (404). Check repo/path exists."
                ) from e
            if e.response.status_code == HTTP_UNAUTHORIZED:
                raise exceptions.BrokerError(
                    f"Unauthorized: {url} (401). Check your GitHub token."
                ) from e
            raise exceptions.BrokerError(f"GitHub API error: {e}") from e

    def resolve_commit(self, ref="master"):
        """Resolve a ref to a commit SHA."""
        data = self._request("GET", f"/commits/{ref}").json()
        return data["sha"]

    def get_file_content(self, path, ref="master"):
        """Get the content of a file from the repository."""
        data = self._request("GET", f"/contents/{path}", params={"ref": ref}).json()
        if isinstance(data, list):
            raise exceptions.BrokerError(f"Path '{path}' is a directory, not a file.")
        if data.get("encoding") != "base64":
            raise exceptions.BrokerError(f"Unexpected encoding: {data.get('encoding')}")
        return base64.b64decode(data["content"])

    def list_remote_scenarios(self, path="", ref="master"):  # noqa: PLR0912
        """List scenarios in the repository."""
        # path is a relative filter prefix
        # 1. Try metadata.yaml first for fast lookup
        if metadata := self.get_metadata(ref):
            # metadata structure isn't fully defined yet but assuming it might contain a list
            # For now, let's assume metadata might not cover everything or trust the API more
            # The spec says "Use metadata when available", implying it contains the file list
            if isinstance(metadata, dict) and "scenarios" in metadata:
                scenarios = []
                for s in metadata["scenarios"]:
                    if isinstance(s, str) and s.startswith(path):
                        scenarios.append(
                            {
                                "path": s,
                                "name": s.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                                "categories": [],
                                "description": None,
                            }
                        )
                    elif isinstance(s, dict) and s.get("path") and s["path"].startswith(path):
                        scenarios.append(
                            {
                                "path": s["path"],
                                "name": s.get(
                                    "name", s["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                                ),
                                "categories": s.get("categories", []),
                                "description": s.get("description"),
                            }
                        )
                # Sort by path for consistency
                return sorted(scenarios, key=lambda x: x["path"])

        # 2. Recursive walk via Contents API
        # GitHub API doesn't support recursive=1 for contents, only for trees
        # Use the Tree API for recursive listing - much more efficient
        try:
            # First need the tree SHA for the ref
            commit_sha = self.resolve_commit(ref)
            commit_obj = self._request("GET", f"/commits/{commit_sha}").json()
            tree_sha = commit_obj.get("commit", {}).get("tree", {}).get("sha")
            if not tree_sha:
                raise exceptions.BrokerError(
                    f"Unable to resolve tree SHA for ref '{ref}' (commit {commit_sha})"
                )
            tree_resp = self._request(
                "GET", f"/git/trees/{tree_sha}", params={"recursive": "1"}
            ).json()

            scenarios = []
            if tree_resp.get("truncated"):
                logger.warning("Repo is too large, results may be truncated.")

            for entry in tree_resp.get("tree", []):
                p = entry["path"]
                if entry["type"] == "blob" and (p.endswith(".yaml") or p.endswith(".yml")):
                    # Exclude top-level files that aren't scenarios (like metadata.yaml) or hidden
                    if "/" not in p and p in ("metadata.yaml", "LICENSE", "README.md"):
                        continue
                    if p.startswith(".") or "/." in p:
                        continue

                    if p.startswith(path):
                        scenarios.append(
                            {
                                "path": p,
                                "name": p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                                "categories": [],
                                "description": None,
                            }
                        )
            return sorted(scenarios, key=lambda x: x["path"])

        except exceptions.BrokerError:
            # Fallback to non-recursive walk if Tree API fails (unlikely)
            return self._walk_contents(path, ref)

    def _walk_contents(self, path, ref):  # Backup method, slow
        results = []
        try:
            items = self._request("GET", f"/contents/{path}", params={"ref": ref}).json()
        except exceptions.BrokerError:
            return []

        if not isinstance(items, list):
            items = [items]

        for item in items:
            if item["type"] == "file":
                if item["name"].endswith((".yaml", ".yml")):
                    p = item["path"]
                    results.append(
                        {
                            "path": p,
                            "name": p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                            "categories": [],
                            "description": None,
                        }
                    )
            elif item["type"] == "dir":
                results.extend(self._walk_contents(item["path"], ref))
        return results


class GitLabAdapter(BaseAdapter):
    """Adapter for GitLab repositories."""

    def __init__(self, host_url, project_path):
        self.host_url = host_url.rstrip("/")
        self.project_path = project_path
        # GitLab API requires URL-encoded project path (namespace/project)
        self.encoded_path = urllib.parse.quote(project_path, safe="")

    def _request(self, method, endpoint, **kwargs):
        url = f"{self.host_url}/api/v4/projects/{self.encoded_path}{endpoint}"
        headers = kwargs.pop("headers", {})
        if token := _get_token(self.host_url):
            headers["PRIVATE-TOKEN"] = token

        def prompt_func():
            return requests.request(method, url, headers=headers, timeout=30, **kwargs)

        try:
            response = helpers.simple_retry(prompt_func, (), {}, max_timeout=30)
            response.raise_for_status()
            return response
        except requests.HTTPError as e:
            if e.response.status_code == HTTP_NOT_FOUND:
                raise exceptions.BrokerError(
                    f"Resource not found: {url} (404). Check repo/path."
                ) from e
            raise exceptions.BrokerError(f"GitLab API error: {e}") from e

    def resolve_commit(self, ref="master"):
        """Resolve a ref to a commit SHA."""
        # Can use branches API or commits API
        try:
            data = self._request("GET", f"/repository/commits/{ref}").json()
            return data["id"]
        except exceptions.BrokerError:
            # Try as a branch/tag if commit fetch fails (ambiguous ref)
            pass
        raise exceptions.BrokerError(f"Could not resolve ref '{ref}' on GitLab.")

    def get_file_content(self, path, ref="master"):
        """Get the content of a file from the repository."""
        # Fetch raw file content
        encoded_file_path = urllib.parse.quote(path, safe="")
        response = self._request(
            "GET", f"/repository/files/{encoded_file_path}/raw", params={"ref": ref}
        )
        return response.content

    def list_remote_scenarios(self, path="", ref="master"):
        """List scenarios in the repository."""
        # Use recursive tree API
        scenarios = []
        page = 1
        while True:
            params = {"path": path, "ref": ref, "recursive": True, "per_page": 100, "page": page}
            data = self._request("GET", "/repository/tree", params=params).json()
            if not data:
                break

            for entry in data:
                p = entry["path"]
                if entry["type"] == "blob" and (p.endswith(".yaml") or p.endswith(".yml")):
                    if "/" not in p and p in ("metadata.yaml", "LICENSE", "README.md"):
                        continue
                    scenarios.append(
                        {
                            "path": p,
                            "name": p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                            "categories": [],
                            "description": None,
                        }
                    )

            page += 1
            if "next" not in self._request("HEAD", "/repository/tree", params=params).links:
                break

        return sorted(scenarios, key=lambda x: x["path"])


class RawHTTPAdapter(BaseAdapter):
    """Adapter for raw HTTP URLs (Gist, raw.githubusercontent, etc)."""

    def __init__(self, url):
        self.url = url

    def resolve_commit(self, ref="master"):
        """Resolve a ref to a commit SHA (not supported for raw URLs)."""
        return None  # No commit usage for raw URLs

    def get_file_content(self, path, ref="master"):
        """Get the content of a file from the URL."""
        # path is ignored here as the URL points to a specific file
        # unless we are processing a manifest list
        target_url = path if path.startswith("http") else self.url

        def prompt_func():
            return requests.get(target_url, timeout=30)

        try:
            response = helpers.simple_retry(prompt_func, (), {}, max_timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            raise exceptions.BrokerError(f"Failed to fetch raw URL {target_url}: {e}") from e

    def list_remote_scenarios(self, path="", ref="master"):
        """List scenarios from the URL."""
        # Check if the text matches a Manifest structure
        content = self.get_file_content(self.url)
        try:
            data = yaml.load(content)
            if isinstance(data, dict) and "manifest" in data:
                scenarios = []
                for item in data["manifest"]:
                    if isinstance(item, str):
                        scenarios.append(
                            {
                                "path": item,
                                "name": item.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                                "categories": [],
                                "description": None,
                            }
                        )
                    elif isinstance(item, dict) and "url" in item:
                        scenarios.append(
                            {
                                "path": item["url"],
                                "name": item.get(
                                    "name", item["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                                ),
                                "categories": item.get("categories", []),
                                "description": item.get("description"),
                            }
                        )
                return scenarios
        except (KeyError, ValueError, YAMLError) as e:
            logger.debug("URL content is not a manifest: %s", e)

        # If --list was explicitly requested in CLI, this raises error
        # But this method might be called internally. The caller (CLI)
        # should handle the error if the user asked for a list.
        # For internal logic, returning [self.url] implies it's a single file import.
        return [
            {
                "path": self.url,
                "name": self.url.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                "categories": [],
                "description": None,
            }
        ]


def _parse_http_source(source_string):  # noqa: PLR0912
    """Parse HTTP/HTTPS URLs into adapters.

    Args:
        source_string: The URL source string

    Returns:
        Tuple of (adapter_instance, relative_path_string, ref_string)
    """
    # Check against known config
    git_hosts = _get_git_hosts()

    # Check for configured GitLab instances first
    for host in git_hosts:
        host_url = host.get("url")
        if not host_url:
            continue
        if source_string.startswith(host_url) and host.get("type") == "gitlab":
            # Extract project path: url/namespace/project/...
            rest = source_string[len(host_url) :].lstrip("/")
            parts = rest.split("/")
            if len(parts) < MIN_REPO_PARTS:
                continue

            namespace = parts[0]
            project = parts[1]
            path_parts = parts[2:]
            ref = "master"

            # Handle web UI URLs: url/namespace/project/-/blob/ref/file.yaml
            # or url/namespace/project/-/tree/ref/dir/
            if path_parts and path_parts[0] == "-":
                if len(path_parts) >= MIN_WEB_UI_PARTS + 1 and path_parts[1] in ("blob", "tree"):
                    ref = path_parts[2]
                    path_parts = path_parts[3:]
                else:
                    path_parts = path_parts[1:]

            return (
                GitLabAdapter(host_url, f"{namespace}/{project}"),
                "/".join(path_parts),
                ref,
            )

    # Check for standard GitLab.com
    if source_string.startswith("https://gitlab.com/"):
        rest = source_string[len("https://gitlab.com/") :].lstrip("/")
        parts = rest.split("/")
        if len(parts) < MIN_GITLAB_PARTS:
            raise exceptions.BrokerError(f"Invalid GitLab source: {source_string}")

        namespace = parts[0]
        project = parts[1]
        path_parts = parts[2:]
        ref = "master"

        # Handle web UI URLs: https://gitlab.com/namespace/project/-/blob/ref/file.yaml
        # or https://gitlab.com/namespace/project/-/tree/ref/dir/
        if path_parts and path_parts[0] == "-":
            if len(path_parts) >= MIN_WEB_UI_PARTS + 1 and path_parts[1] in ("blob", "tree"):
                ref = path_parts[2]
                path_parts = path_parts[3:]
            else:
                path_parts = path_parts[1:]

        return (
            GitLabAdapter("https://gitlab.com", f"{namespace}/{project}"),
            "/".join(path_parts),
            ref,
        )

    # Check for standard GitHub.com
    if source_string.startswith("https://github.com/"):
        rest = source_string[len("https://github.com/") :].lstrip("/")
        parts = rest.split("/")
        if len(parts) < MIN_REPO_PARTS:
            raise exceptions.BrokerError(f"Invalid GitHub source: {source_string}")

        owner = parts[0]
        repo = parts[1]
        path_parts = parts[2:]
        ref = "master"

        # Handle web UI URLs: https://github.com/owner/repo/blob/ref/file.yaml
        # or https://github.com/owner/repo/tree/ref/dir/
        if path_parts and path_parts[0] in ("blob", "tree"):
            if len(path_parts) < MIN_WEB_UI_PARTS:
                raise exceptions.BrokerError(f"Invalid GitHub web URL: {source_string}")
            ref = path_parts[1]
            path_parts = path_parts[2:]

        return GitHubAdapter(owner, repo), "/".join(path_parts), ref

    # Fallback to RawHTTPAdapter for everything else (Gists, raw files, etc.)
    return RawHTTPAdapter(source_string), "", "master"


def parse_source(source_string):
    """Parse a source string and return an adapter instance and relative path.

    Args:
        source_string: The raw source passed to the CLI (e.g., 'owner/repo',
                       'gitlab.com/owner/repo', 'https://...', etc.)

    Returns:
        Tuple of (adapter_instance, relative_path_string, ref_string)
    """
    # Handle @ref syntax (warn and strip)
    if "@" in source_string:
        base, requested_ref = source_string.split("@", 1)
        if requested_ref and requested_ref != "master":
            logger.warning(
                f"Ignoring requested ref '{requested_ref}' - 'master' is the only supported branch."
            )
        source_string = base

    # 1. Raw HTTP / HTTPS URL
    if source_string.startswith(("http://", "https://")):
        return _parse_http_source(source_string)

    # 2. GitLab.com shortcut: gitlab.com/owner/repo[/path]
    if source_string.startswith("gitlab.com/"):
        parts = source_string.split("/")
        if len(parts) < MIN_GITLAB_PARTS:
            raise UsageError("Invalid GitLab source. Expected: gitlab.com/owner/repo[/path]")
        return (
            GitLabAdapter("https://gitlab.com", f"{parts[1]}/{parts[2]}"),
            "/".join(parts[3:]),
            "master",
        )

    # 3. GitHub shortcut: owner/repo[/path]
    # Simplest case, assumes it's not a URL and doesn't start with a domain-like prefix
    parts = source_string.split("/")
    if len(parts) >= MIN_REPO_PARTS:
        return GitHubAdapter(parts[0], parts[1]), "/".join(parts[2:]), "master"

    raise UsageError(f"Invalid source format: {source_string}")
