#!/usr/bin/python3

import functools
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import click
import git
import sh
from packit.config.package_config import get_local_specfile_path
from rebasehelper.specfile import SpecFile
from yaml import dump

logger = logging.getLogger(__name__)

# build/test
TARGETS = ["centos-stream-x86_64"]
START_TAG = "sg-start"


@click.group("dist2src")
@click.option(
    "-v", "--verbose", count=True, help="Increase verbosity. Repeat to log more."
)
def cli(verbose):
    """Script to convert the tip of a branch from a dist-git repository
    into a commit on a branch in a source-git repository.

    As of now, this downloads the sources from the lookaside cache,
    unpacks them, applies all the patches, and remove the applied patches
    from the SPEC-file.

    For example to convert git.centos.org/rpms/rpm, branch 'c8s', to a
    source-git repo, with a branch also called 'c8s', in one step:

        \b
        $ cd git.centos.org
        $ dist2src convert rpms/rpm:c8s src/rpm:c8s

    For the same, but doing each conversion step separately:

        \b
        $ cd git.centos.org
        $ dist2src checkout rpms/rpm c8s
        $ dist2src checkout --orphan src/rpm c8s
        $ dist2src get-archive rpms/rpm
        $ dist2src extract-archive rpms/rpm src/rpm
        $ dist2src copy-spec rpms/rpm src/rpm
        $ dist2src add-packit-config src/rpm
        $ dist2src copy-patches rpms/rpm src/rpm
        $ dist2src apply-patches src/rpm
    """
    logger.addHandler(logging.StreamHandler())
    if verbose > 1:
        logger.setLevel(logging.DEBUG)
    elif verbose > 0:
        logger.setLevel(logging.INFO)


def log_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        args_string = ", ".join([repr(a) for a in args])
        kwargs_string = ", ".join([f"{k}={v!r}" for k, v in kwargs.items()])
        sep = ", " if args_string and kwargs_string else ""
        logger.debug(f"{func.__name__}({args_string}{sep}{kwargs_string})")
        ret = func(*args, **kwargs)
        return ret

    return wrapper


@cli.command()
@click.argument("path", type=click.Path(file_okay=False))
@click.argument("branch", type=click.STRING)
@click.option(
    "--orphan", is_flag=True, help="Create an branch with disconnected history."
)
@log_call
def checkout(path, branch, orphan=False):
    """Checkout a Git repository.

    This will create the directory at PATH, if it doesn't exist already,
    and initialize it as a Git repository. The later is not destructive
    in an existing directory.

    Checking out BRANCH is done with the `-B` flag, which means the
    branch is created if it doesn't exist or reset, if it does.
    """
    if path.startswith(os.pardir):
        raise ValueError("This is bad, don't start path with '..'")

    if not os.path.exists(path):
        os.makedirs(path)

    repo = git.Repo.init(path)
    options = {}
    if orphan:
        options["orphan"] = branch

    if options:
        repo.git.checkout(**options)
    else:
        repo.git.checkout(branch)


@cli.command()
@click.argument("gitdir", type=click.Path(exists=True, file_okay=False))
@log_call
def get_archive(gitdir):
    """Calls get_sources.sh in GITDIR.

    GITDIR needs to be a dist-git repository.

    Set DIST2SRC_GET_SOURCES to the path to git_sources.sh, if it's not
    in the PATH.
    """
    script = os.getenv("DIST2SRC_GET_SOURCES", "get_sources.sh")
    command = sh.Command(script)

    with sh.pushd(gitdir):
        logger.debug(f"Running command in {os.getcwd()}")
        stdout = command()

    return stdout


def _copy_files(origin: str, dest: str, origin_dir: str, dest_dir: str, glob: str) -> None:
    """
    Copy all glob files from origin/origin_dir to dest/dest_dir
    """
    origin_ = Path(origin, origin_dir)
    dest_ = Path(dest, dest_dir)

    dest_.mkdir(parents=True, exist_ok=True)

    for file_ in origin_.glob(glob):
        shutil.copy2(file_, dest_ / file_.name)


@cli.command()
@click.argument("origin", type=click.Path(exists=True, file_okay=False))
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@log_call
@click.pass_context
def copy_spec(ctx, origin, dest):
    """Copy 'SPECS/*.spec' from a dist-git repo to a source-git repo."""
    _copy_files(origin=origin, dest=dest, origin_dir="SPECS", dest_dir="SPECS", glob="*.spec")


@cli.command()
@click.argument("origin", type=click.Path(exists=True, file_okay=False))
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@log_call
@click.pass_context
def copy_all_sources(ctx, origin, dest):
    """Copy 'SOURCES/*' from a dist-git repo to a source-git repo."""
    _copy_files(origin=origin, dest=dest, origin_dir="SOURCES", dest_dir="SPECS", glob="*")


@cli.command()
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@log_call
@click.pass_context
def add_packit_config(ctx, dest: Path):
    config = {
        # e.g. qemu-kvm ships "some" spec file in their tarball
        # packit doesn't need to look for the spec when we know where it is
        "specfile_path": f"SPECS/{dest.name}.spec",
        "upstream_ref": START_TAG,
        "jobs": [
            {
                "job": "copr_build",
                "trigger": "pull_request",
                "metadata": {"targets": TARGETS},
            },
            {
                "job": "tests",
                "trigger": "pull_request",
                "metadata": {"targets": TARGETS},
            },
        ],
    }
    Path(dest, ".packit.yaml").write_text(dump(config))
    ctx.invoke(stage, gitdir=dest, add=".packit.yaml")
    ctx.invoke(commit, m=".packit.yaml", gitdir=dest)


@cli.command()
@click.argument("origin", type=click.Path(exists=True, file_okay=False))
@click.argument("dest", type=click.Path(exists=True, file_okay=False))
@log_call
@click.pass_context
def extract_archive(ctx, origin, dest):
    """Extract the source archive found in ORIGIN to DEST.

    First, make sure that the archive was downloaded.

    After extracting the archive, stage and commit the changes in DEST.
    """
    # Make sure, the archives exist and use the STDOUT of get_sources.sh
    # to find out their paths.
    stdout = ""
    all_downloaded = False
    while not all_downloaded:
        stdout = ctx.invoke(get_archive, gitdir=origin)
        all_downloaded = all(filter(lambda x: " exists" in x, stdout.strip().split("\n")))
    archives = []
    for line in stdout.strip().split("\n"):
        logger.debug(f"Line: {line}")
        archive = Path(origin, line.partition(" exists")[0])
        logger.debug(f"Archive: {archive}")
        archives.append(archive)

    for archive in archives:
        with tempfile.TemporaryDirectory() as tmpdir:
            shutil.unpack_archive(archive, tmpdir)
            # Expect an archive with a single directory.
            assert len(os.listdir(tmpdir)) == 1
            topdir = Path(tmpdir, os.listdir(tmpdir)[0])
            # These are all the files under the directory that was
            # in the archive.
            for f in topdir.iterdir():
                shutil.move(f, Path(dest, f.name))

    ctx.invoke(stage, gitdir=dest)
    ctx.invoke(commit, m="Unpack archive", gitdir=dest)


@cli.command()
@click.argument("gitdir", type=click.Path(exists=True, file_okay=False))
@log_call
@click.pass_context
def apply_patches(ctx, gitdir):
    """Apply the patches used in the SPEC-file found in GITDIR.

    Apply all the patches used in the SPEC-file, then update the
    SPEC-file by commenting the patches that were applied and deleting
    those patches from the disk.

    Stage and commit changes after each patch, except the ones in the
    'centos-packaging' directory.
    """

    class Specfile(SpecFile):
        def comment_patches(self, patch_indexes):
            pattern = re.compile(r"^Patch(?P<index>\d+)\s*:.+$")
            package = self.spec_content.section("%package")
            for i, line in enumerate(package):
                match = pattern.match(line)
                if match:
                    index = int(match.group("index"))
                    if index in patch_indexes:
                        logger.debug(f"Commenting patch {index}")
                        package[i] = f"# {line}"
            self.spec_content.replace_section("%package", package)

    specdir = Path(gitdir, "SPECS")
    specpath = specdir / get_local_specfile_path(specdir)
    logger.info(f"specpath = {specpath}")
    specfile = Specfile(
        specpath,
        sources_location=str(Path(gitdir, "SPECS")),
    )
    repo = git.Repo(gitdir)
    applied_patches = specfile.get_applied_patches()

    # TODO(csomh):
    # the bellow is not complete, as there are many more ways to specify
    # patches in spec files. Cover this in the future.
    patch_indices = [p.index for p in applied_patches]
    # comment out all Patch in %package
    specfile.comment_patches(patch_indices)
    # comment out all %patch in %prep
    specfile._process_patches(patch_indices)
    specfile.save()
    repo.git.add(specpath.relative_to(gitdir))
    repo.git.commit(m="Downstream spec with commented patches")

    # Create a tag marking last commit before downstream patches
    logger.info(f"Creating tag {START_TAG}")
    repo.create_tag(START_TAG)

    # Transfer all patches that were in spec into git commits ('git am' or 'git apply')
    for patch in applied_patches:
        message = f"Apply Patch{patch.index}: {patch.get_patch_name()}"
        logger.info(message)
        rel_path = os.path.relpath(patch.path, gitdir)
        try:
            repo.git.am(rel_path)
        except git.exc.CommandError as e:
            logger.debug(str(e))
            repo.git.apply(rel_path, p=patch.strip)
            ctx.invoke(stage, gitdir=gitdir, exclude="SPECS")
            ctx.invoke(commit, gitdir=gitdir, m=message)
        # The patch is a commit now, so clean it up.
        os.unlink(patch.path)


@cli.command()
@click.argument("gitdir", type=click.Path(exists=True, file_okay=False))
@click.option("-m", default="Import sources from dist-git", help="Git commmit message")
@log_call
def commit(gitdir, m):
    """Commit staged changes in GITDIR."""
    repo = git.Repo(gitdir)
    repo.git.commit(m=m)


@cli.command()
@click.argument("gitdir", type=click.Path(exists=True, file_okay=False))
@click.option("--add", help="Files to add content from. Accepts globs (e.g. *.spec).")
@click.option("--exclude", help="Path to exclude from staging, relative to GITDIR")
@log_call
def stage(gitdir, add=None, exclude=None):
    """Stage content in GITDIR."""
    repo = git.Repo(gitdir)
    if exclude:
        exclude = f":(exclude){exclude}"
        logger.debug(exclude)
    repo.git.add(add or ".", exclude)


@cli.command()
@click.argument("origin", type=click.STRING)
@click.argument("dest", type=click.STRING)
@log_call
@click.pass_context
def convert(ctx, origin, dest):
    """Convert a dist-git repository into a source-git repository.

    ORIGIN and DEST are in the format of

        REPO_PATH:BRANCH

    This command calls all the other commands.
    """
    origin_dir, origin_branch = origin.split(":")
    dest_dir, dest_branch = dest.split(":")

    ctx.invoke(checkout, path=origin_dir, branch=origin_branch)
    ctx.invoke(checkout, path=dest_dir, branch=dest_branch, orphan=True)
    ctx.invoke(get_archive, gitdir=origin_dir)
    ctx.invoke(extract_archive, origin=origin_dir, dest=dest_dir)
    ctx.invoke(copy_spec, origin=origin_dir, dest=dest_dir)
    ctx.invoke(add_packit_config, dest=Path(dest_dir))
    ctx.invoke(copy_all_sources, origin=origin_dir, dest=dest_dir)
    ctx.invoke(apply_patches, gitdir=dest_dir)


if __name__ == "__main__":
    cli()
