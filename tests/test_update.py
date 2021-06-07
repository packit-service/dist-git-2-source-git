# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import subprocess
from pathlib import Path
from typing import Union

import git
import pytest

from dist2src.constants import START_TAG_TEMPLATE
import unittest

from requre.helpers.simple_object import Simple
from requre.helpers.files import StoreFiles
from requre.online_replacing import (
    apply_decorator_to_all_methods,
    replace_module_match,
)
from requre.modules_decorate_all_methods import (
    record_git_module,
    record_requests_module,
    record_tempfile_module,
)

from tests.conftest import (
    MOCK_BUILD,
    TEST_PROJECTS_WITH_BRANCHES,
    TEST_PROJECTS_WITH_BRANCHES_SINGLE_COMMIT,
    run_dist2src,
    run_packit,
    clone_package_rpms,
    clone_package_src,
)


@record_requests_module
@record_git_module
@record_tempfile_module
@apply_decorator_to_all_methods(
    replace_module_match(
        what="packit.utils.run_command_remote", decorate=Simple.decorator_plain()
    )
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="packit.fedpkg.FedPKG.clone",
        decorate=StoreFiles.where_arg_references(
            key_position_params_dict={"target_path": 2}
        ),
    )
)
# Be aware that decorator stores login and token to test_data, replace it by some value.
# Default precommit hook doesn't do that for copr.v3.helpers, see README.md
@apply_decorator_to_all_methods(
    replace_module_match(
        what="copr.v3.helpers.config_from_file", decorate=Simple.decorator_plain()
    )
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="tests.conftest.clone_package",
        decorate=StoreFiles.where_arg_references(
            key_position_params_dict={"dist_git_path": 1},
            return_decorator=Simple.decorator_plain,
        ),
    )
)
class Update(unittest.TestCase):
    def setUp(self) -> None:
        self.package_name = "haproxy"
        self.branch = "c8s"
        self.tmp_path = Path("/tmp") / "test_update"
        self.dist_git_path = self.tmp_path / "d" / self.package_name
        self.sg_path = self.tmp_path / "s" / self.package_name
        self.dist_git_path.mkdir(parents=True)
        self.sg_path.mkdir(parents=True)

    def test_update(self):
        """
        perform an update from a previous dist-git commit (HEAD~1)
        to the last one (HEAD)
        """
        clone_package(
            self.package_name, dist_git_path=str(self.dist_git_path), branch=self.branch
        )
        subprocess.check_call(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=self.dist_git_path,
        )

        run_dist2src(
            [
                "-vvv",
                "convert",
                f"{self.dist_git_path}:{self.branch}",
                f"{self.sg_path}:{self.branch}",
            ]
        )
        # the source-git repo cannot be dirty after the conversion
        assert_repo_is_not_dirty(self.sg_path)

        subprocess.check_call(
            ["git", "pull", "origin", self.branch],
            cwd=self.dist_git_path,
        )

        run_dist2src(
            [
                "-vvv",
                "convert",
                f"{self.dist_git_path}:{self.branch}",
                f"{self.sg_path}:{self.branch}",
            ]
        )
        assert_repo_is_not_dirty(self.sg_path)

        run_packit(
            [
                "--debug",
                "srpm",
            ],
            working_dir=self.sg_path,  # _srcrpmdir rpm macro is set to /, let's CWD then
        )
        srpm_path = next(self.sg_path.glob("*.src.rpm"))
        assert srpm_path.exists()
        if MOCK_BUILD:
            subprocess.check_call(
                [
                    "mock",
                    "--rebuild",
                    "-r",
                    "centos-stream-x86_64",
                    srpm_path,
                ]
            )


def assert_repo_is_not_dirty(repo_path: Union[str, Path]):
    is_dirty = subprocess.check_output(["git", "status", "--short"], cwd=repo_path)
    assert not is_dirty


@pytest.mark.slow
@pytest.mark.parametrize("package_name,branch", TEST_PROJECTS_WITH_BRANCHES)
def test_update(tmp_path: Path, package_name, branch):
    """
    perform an update from a previous dist-git commit (HEAD~1)
    to the last one (HEAD)
    """
    dist_git_path = tmp_path / "d" / package_name
    sg_path = tmp_path / "s" / package_name
    dist_git_path.mkdir(parents=True)
    sg_path.mkdir(parents=True)

    clone_package_rpms(package_name, str(dist_git_path), branch=branch)
    subprocess.check_call(
        ["git", "reset", "--hard", "HEAD~1"],
        cwd=dist_git_path,
    )

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    # the source-git repo cannot be dirty after the conversion
    assert_repo_is_not_dirty(sg_path)

    subprocess.check_call(
        ["git", "pull", "origin", branch],
        cwd=dist_git_path,
    )

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    assert_repo_is_not_dirty(sg_path)

    run_packit(
        ["--debug", "srpm", "."],
        working_dir=sg_path,  # _srcrpmdir rpm macro is set to /, let's CWD then
    )
    srpm_path = next(sg_path.glob("*.src.rpm"))
    assert srpm_path.exists()
    if MOCK_BUILD:
        subprocess.check_call(
            [
                "mock",
                "--rebuild",
                "-r",
                "centos-stream-x86_64",
                srpm_path,
            ]
        )


@pytest.mark.slow
@pytest.mark.parametrize(
    "package_name,branch", TEST_PROJECTS_WITH_BRANCHES_SINGLE_COMMIT
)
def test_update_from_same_commit(tmp_path: Path, package_name, branch):
    """
    Run an update twice from the same commit.

    This is to check the an initial source-git generation and updating an
    existing source-git repo produces the same result (the same commits
    and content).
    """
    dist_git_path = tmp_path / "d" / package_name
    sg_path = tmp_path / "s" / package_name
    dist_git_path.mkdir(parents=True)
    sg_path.mkdir(parents=True)

    clone_package_rpms(package_name, str(dist_git_path), branch=branch)

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    # the source-git repo cannot be dirty after the conversion
    assert_repo_is_not_dirty(sg_path)

    sg_repo = git.Repo(path=sg_path)
    upstream_ref = START_TAG_TEMPLATE.format(branch=branch)
    first_round_commits = list(sg_repo.iter_commits(f"{upstream_ref}..HEAD"))

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    assert_repo_is_not_dirty(sg_path)

    second_round_commits = list(sg_repo.iter_commits(f"{upstream_ref}..HEAD"))

    run_packit(
        ["--debug", "srpm", "."],
        working_dir=sg_path,  # _srcrpmdir rpm macro is set to /, let's CWD then
    )
    srpm_path = next(sg_path.glob("*.src.rpm"))
    assert srpm_path.exists()

    # Check that patch commits are same
    assert len(first_round_commits) == len(second_round_commits)
    # we cannot use git-diff here b/c theu may be additional changes
    # to tree from %prep after the first conversion
    for first, second in zip(first_round_commits, second_round_commits):
        # we could also use first.stats here but it's horribly
        # inefficient and takes minutes to evaluate
        for idx, tree in enumerate(first.tree.trees):
            if tree.path == "autom4te.cache":  # cache, which can change
                continue
            assert tree == second.tree.trees[idx]
        assert first.tree.blobs == second.tree.blobs

    if MOCK_BUILD:
        subprocess.check_call(
            [
                "mock",
                "--rebuild",
                "-r",
                "centos-stream-x86_64",
                srpm_path,
            ]
        )


@pytest.mark.slow
@pytest.mark.parametrize(
    "package_name,branch,old_version",
    [
        ("rpm", "c8s", "imports/c8s/rpm-4.14.2-37.el8"),
        ("systemd", "c8s", "imports/c8/systemd-239-13.el8_0.3"),
    ],
)
def test_update_source(tmp_path: Path, package_name, branch, old_version):
    """perform an update from a specific ref to the last commit"""
    dist_git_path = tmp_path / "d" / package_name
    sg_path = tmp_path / "s" / package_name
    dist_git_path.mkdir(parents=True)
    sg_path.mkdir(parents=True)

    clone_package_rpms(package_name, str(dist_git_path), branch=branch)
    subprocess.check_call(
        ["git", "reset", "--hard", old_version],
        cwd=dist_git_path,
    )

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    # the source-git repo cannot be dirty after the conversion
    assert_repo_is_not_dirty(sg_path)

    subprocess.check_call(
        ["git", "pull", "origin", branch],
        cwd=dist_git_path,
    )

    run_dist2src(
        ["-vvv", "convert", f"{dist_git_path}:{branch}", f"{sg_path}:{branch}"]
    )
    assert_repo_is_not_dirty(sg_path)

    run_packit(
        ["--debug", "srpm", "."],
        working_dir=sg_path,  # _srcrpmdir rpm macro is set to /, let's CWD then
    )
    srpm_path = next(sg_path.glob("*.src.rpm"))
    assert srpm_path.exists()
    if MOCK_BUILD:
        subprocess.check_call(
            [
                "mock",
                "--rebuild",
                "-r",
                "centos-stream-x86_64",
                srpm_path,
            ]
        )


@pytest.mark.slow
@pytest.mark.parametrize(
    "package",
    (
        "apr",
        "pacemaker",
        "vala",
        "upower",
    ),
)
def test_update_existing(tmp_path: Path, package):
    """
    Jirka found an issue that we cannot update several packages
    which were created by an old version of d2s, one of them is apr
    """
    dist_git_path = tmp_path / "d" / package
    dg_branch = "c8s"
    source_git_path = tmp_path / "s" / package
    sg_branch = "c8s"
    clone_package_rpms(package, str(dist_git_path), branch=dg_branch)
    clone_package_src(package, str(source_git_path), branch=sg_branch)
    run_dist2src(
        [
            "-vvv",
            "convert",
            f"{dist_git_path}:{dg_branch}",
            f"{source_git_path}:{sg_branch}",
        ]
    )
    assert_repo_is_not_dirty(source_git_path)
    run_packit(
        ["--debug", "srpm", "."],
        working_dir=source_git_path,
    )
    srpm_path = next(source_git_path.glob("*.src.rpm"))
    assert srpm_path.exists()


@pytest.mark.slow
def test_update_catch(tmp_path: Path):
    """
    make sure we can update package catch and
    check the repo is in expected state after the update
    """
    package = "catch"
    dist_git_path = tmp_path / "d" / package
    dg_branch = "c8"
    source_git_path = tmp_path / "s" / package
    sg_branch = "c8"
    clone_package_rpms(package, str(dist_git_path), branch=dg_branch)
    clone_package_src(
        package,
        str(source_git_path),
        branch=sg_branch,
    )
    run_dist2src(
        [
            "-vvv",
            "convert",
            f"{dist_git_path}:{dg_branch}",
            f"{source_git_path}:{sg_branch}",
        ]
    )
    assert_repo_is_not_dirty(source_git_path)
    run_packit(
        ["--debug", "srpm", "."],
        working_dir=source_git_path,
    )
    srpm_path = next(source_git_path.glob("*.src.rpm"))
    assert srpm_path.exists()
    git_log_out = subprocess.check_output(
        ["git", "log", "--pretty=format:%s", "origin/c8.."], cwd=source_git_path
    ).decode()
    # the line below is really fragile
    # if it breaks, navigate to the source-git repo and check git history
    assert (
        git_log_out
        == """Changes after running %prep
Add sources defined in the spec file
Add spec-file for the distribution
.packit.yaml
catch-2.2.1 base
Prepare for a new update"""
    )
