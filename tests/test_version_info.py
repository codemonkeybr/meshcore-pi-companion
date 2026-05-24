from app import version_info


class TestAppBuildInfo:
    def setup_method(self):
        version_info.get_app_build_info.cache_clear()

    def teardown_method(self):
        version_info.get_app_build_info.cache_clear()

    def test_prefers_package_metadata_and_git(self, monkeypatch):
        monkeypatch.setattr(version_info, "_package_metadata_version", lambda: "3.4.1")
        monkeypatch.setattr(version_info, "_env_version", lambda: "3.4.0-env")
        monkeypatch.setattr(version_info, "_build_info_version", lambda build_info: "3.3.0-build")
        monkeypatch.setattr(version_info, "_pyproject_version", lambda root: "3.2.0-pyproject")
        monkeypatch.setattr(version_info, "_git_output", lambda root, *args: "abcdef12")
        monkeypatch.setattr(version_info, "_env_commit_hash", lambda: "fedcba0987654321")
        monkeypatch.setattr(version_info, "_build_info_commit_hash", lambda build_info: "11223344")

        info = version_info.get_app_build_info()

        assert info.version == "3.4.1"
        assert info.version_source == "package_metadata"
        assert info.commit_hash == "abcdef12"
        assert info.commit_source == "git"

    def test_falls_back_to_pyproject_and_build_info(self, monkeypatch):
        monkeypatch.setattr(version_info, "_package_metadata_version", lambda: None)
        monkeypatch.setattr(version_info, "_env_version", lambda: None)
        monkeypatch.setattr(version_info, "_build_info_version", lambda build_info: None)
        monkeypatch.setattr(version_info, "_pyproject_version", lambda root: "3.2.0")
        monkeypatch.setattr(version_info, "_git_output", lambda root, *args: None)
        monkeypatch.setattr(version_info, "_env_commit_hash", lambda: None)
        monkeypatch.setattr(version_info, "_build_info_commit_hash", lambda build_info: "cf1a55e2")

        info = version_info.get_app_build_info()

        assert info.version == "3.2.0"
        assert info.version_source == "pyproject"
        assert info.commit_hash == "cf1a55e2"
        assert info.commit_source == "build_info"
