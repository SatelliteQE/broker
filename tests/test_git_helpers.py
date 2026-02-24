"""Tests for Git helpers and repo adapters."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from broker.helpers import git


@pytest.fixture
def mock_settings(monkeypatch):
    """Mock broker settings."""
    settings_dict = {
        "SCENARIO_IMPORT": {
            "git_hosts": [
                {"url": "https://git.example.com", "token": "example-token", "type": "gitlab"},
                {"url": "https://github.com", "token": "gh-token", "type": "github"}
            ]
        }
    }
    mock_obj = MagicMock()
    mock_obj.get.side_effect = lambda k, d=None: settings_dict.get(k, d)
    mock_obj.__getitem__.side_effect = lambda k: settings_dict[k]

    monkeypatch.setattr(git, "settings", mock_obj)
    return settings_dict


def test_parse_source(mock_settings):
    """Test source parsing for various formats."""
    # GitHub shortcut
    adapter, path, ref = git.parse_source("owner/repo")
    assert isinstance(adapter, git.GitHubAdapter)
    assert adapter.owner == "owner"
    assert adapter.repo == "repo"
    assert path == ""
    assert ref == "master"

    # GitHub shortcut with path
    adapter, path, ref = git.parse_source("owner/repo/subdir/file.yaml")
    assert isinstance(adapter, git.GitHubAdapter)
    assert path == "subdir/file.yaml"

    # GitLab shortcut
    adapter, path, ref = git.parse_source("gitlab.com/owner/repo/file.yaml")
    assert isinstance(adapter, git.GitLabAdapter)
    assert adapter.host_url == "https://gitlab.com"
    assert adapter.project_path == "owner/repo"
    assert path == "file.yaml"

    # Raw HTTPS URL (Generic)
    adapter, path, ref = git.parse_source("https://example.com/file.yaml")
    assert isinstance(adapter, git.RawHTTPAdapter)
    assert adapter.url == "https://example.com/file.yaml"

    # Raw HTTPS - Known GitHub
    adapter, path, ref = git.parse_source("https://github.com/owner/repo/blob/master/file.yaml")
    assert isinstance(adapter, git.GitHubAdapter)
    assert adapter.owner == "owner"
    assert adapter.repo == "repo"
    assert path == "file.yaml"
    assert ref == "master"

    # Configured GitLab instance
    adapter, path, ref = git.parse_source("https://git.example.com/group/project/file.yaml")
    assert isinstance(adapter, git.GitLabAdapter)
    assert adapter.host_url == "https://git.example.com"
    assert adapter.project_path == "group/project"
    assert path == "file.yaml"

    # @ref syntax (stripped with warning)
    adapter, path, ref = git.parse_source("owner/repo@v1.0")
    assert ref == "master"  # Enforced


def test_uppercase_git_hosts_config(monkeypatch):
    """Support SCENARIO_IMPORT.GIT_HOSTS when keys are normalized by Dynaconf."""
    settings_dict = {
        "SCENARIO_IMPORT": {
            "GIT_HOSTS": [
                {
                    "url": "https://git.upper.example.com",
                    "token": "upper-token",
                    "type": "gitlab",
                }
            ]
        }
    }
    monkeypatch.setattr(git, "settings", settings_dict)

    adapter, path, ref = git.parse_source("https://git.upper.example.com/group/project/file.yaml")
    assert isinstance(adapter, git.GitLabAdapter)
    assert adapter.host_url == "https://git.upper.example.com"
    assert adapter.project_path == "group/project"
    assert path == "file.yaml"
    assert ref == "master"
    assert git._get_token("https://git.upper.example.com/group/project") == "upper-token"


def test_github_adapter_resolve_commit():
    """Test resolving commit SHA on GitHub."""
    adapter = git.GitHubAdapter("owner", "repo")

    with patch("requests.request") as mock_req, \
         patch("broker.helpers.git._get_token", return_value=None):
        mock_req.return_value.status_code = 200
        mock_req.return_value.json.return_value = {"sha": "12345abcdef"}

        sha = adapter.resolve_commit("master")
        assert sha == "12345abcdef"
        mock_req.assert_called_with(
            "GET", "https://api.github.com/repos/owner/repo/commits/master", headers={}, timeout=30
        )


def test_gitlab_adapter_resolve_commit():
    """Test resolving commit SHA on GitLab."""
    adapter = git.GitLabAdapter("https://gitlab.com", "owner/repo")

    with patch("requests.request") as mock_req:
        mock_req.return_value.status_code = 200
        mock_req.return_value.json.return_value = {"id": "12345abcdef"}

        sha = adapter.resolve_commit("master")
        assert sha == "12345abcdef"


def test_raw_http_adapter_manifest():
    """Test raw HTTP adapter with manifest parsing."""
    adapter = git.RawHTTPAdapter("https://example.com/manifest.yaml")

    manifest_content = b"""
    manifest:
      - https://example.com/s1.yaml
      - url: https://example.com/s2.yaml
    """

    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = manifest_content

        scenarios = adapter.list_remote_scenarios()
        paths = [s["path"] for s in scenarios]
        assert "https://example.com/s1.yaml" in paths
        assert "https://example.com/s2.yaml" in paths


def test_retry_behavior():
    """Test that requests are retried."""
    adapter = git.RawHTTPAdapter("https://example.com/file")

    with patch("requests.get") as mock_get:
        # Fail twice, succeed third time
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Fail 1"),
            requests.exceptions.ConnectionError("Fail 2"),
            MagicMock(status_code=200, content=b"success")
        ]

        content = adapter.get_file_content("path")
        assert content == b"success"
        assert mock_get.call_count == 3  # noqa: PLR2004


def test_github_adapter_metadata_parsing(mock_settings):
    """Test GitHub adapter parsing metadata.yaml with mixed content."""
    adapter = git.GitHubAdapter("owner", "repo")

    metadata_content = {
        "scenarios": [
            "path/to/scenario1.yaml",
            {"path": "path/to/scenario2.yaml", "name": "Scenario 2"},
            {"other": "ignore_me"}
        ]
    }

    with patch.object(adapter, "get_metadata", return_value=metadata_content):
        # We don't need to mock _request because get_metadata is mocked
        # and list_remote_scenarios should return early
        scenarios = adapter.list_remote_scenarios()

        paths = [s["path"] for s in scenarios]
        assert "path/to/scenario1.yaml" in paths
        assert "path/to/scenario2.yaml" in paths
        assert len(scenarios) == 2  # noqa: PLR2004
