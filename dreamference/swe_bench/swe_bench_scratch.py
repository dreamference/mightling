"""The host's reads and writes in an instance's scratch, which never follow a link.

The scratch is mounted read-write into the instance's container, and the container runs the
instance image's own programs (its `bash`, its `git`, its tests) as this user. Anything below
the scratch's root may therefore be a symbolic link the image planted: a host read through
`patch.diff -> ../../../../.ssh/id_ed25519` would submit that key as the prediction, and a host
write through `restart-diff.patch -> ../../../../.bashrc` would put the container's text there.
A FIFO or a huge file would hang or exhaust the host.

So every path below the root is opened component by component with `O_NOFOLLOW` against the
directory opened before it, a read takes a regular file only and at most `READ_LIMIT` bytes,
and a write goes to a fresh file in the same directory and is renamed over the name (a rename
replaces a link, it never writes through one). The root itself is the host's (a merged run
links it on purpose), and is followed.
"""

import contextlib
import errno
import fnmatch
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final

# Far beyond any patch, log or rollout an instance writes.
READ_LIMIT: Final[int] = 256 << 20

PARENT: Final[str] = ".."

Relative = str | PurePosixPath


class SweBenchScratch:
    """Link-free file access below a directory a container can write."""

    @classmethod
    def parts(cls, relative: Relative) -> list[str]:
        """Splits a path below the root.

        Args:
            relative: A path below the root.

        Returns:
            list[str]: Its components.

        Raises:
            ValueError: For an empty or absolute path, or one with `..`.
        """
        path = PurePosixPath(relative)
        if path.is_absolute() or not path.parts or PARENT in path.parts:
            raise ValueError(f"not a path below the scratch: {relative}")
        return list(path.parts)

    @classmethod
    def open_directory(cls, root: Path, parts: list[str], create: bool = False) -> int:
        """Opens a directory below the root, one component at a time.

        Args:
            root: The scratch's root (followed).
            parts: The directories below it, none of which may be a link.
            create: Make the missing ones.

        Returns:
            int: A descriptor of the last directory; the caller closes it.
        """
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in parts:
                if create:
                    cls.make_directory(descriptor, part)
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @classmethod
    def make_directory(cls, directory: int, name: str) -> None:
        """Makes a directory unless the name exists (a link there is refused when it is opened).

        Args:
            directory: A descriptor of its parent.
            name: Its name.
        """
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o755, dir_fd=directory)

    @classmethod
    def open(cls, root: Path, relative: Relative) -> BinaryIO:
        """Opens a regular file for reading.

        Args:
            root: The scratch's root.
            relative: The file below it.

        Returns:
            BinaryIO: The open file.

        Raises:
            OSError: When it is missing, a link, or not a regular file.
        """
        parts = cls.parts(relative)
        directory = cls.open_directory(root, parts[:-1])
        try:
            # Non-blocking, so a FIFO opens at once and is refused below instead of waiting.
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory,
            )
        finally:
            os.close(directory)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(errno.EINVAL, f"not a regular file: {relative}")
            os.set_blocking(descriptor, True)
        except BaseException:
            os.close(descriptor)
            raise
        return os.fdopen(descriptor, "rb")

    @classmethod
    def read_text(cls, root: Path, relative: Relative) -> str:
        """Reads a file's text.

        Args:
            root: The scratch's root.
            relative: The file below it.

        Returns:
            str: Its text (at most `READ_LIMIT` bytes, undecodable bytes replaced); empty when it
            cannot be read.
        """
        try:
            with cls.open(root, relative) as handle:
                return handle.read(READ_LIMIT).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    @classmethod
    def write_text(cls, root: Path, relative: Relative, text: str) -> None:
        """Replaces a file (its directories are made), never writing through a link.

        Args:
            root: The scratch's root.
            relative: The file below it.
            text: Its new text.

        Raises:
            OSError: When it cannot be written.
        """
        parts = cls.parts(relative)
        directory = cls.open_directory(root, parts[:-1], create=True)
        staging = f".{parts[-1]}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                staging,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
                dir_fd=directory,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(text.encode("utf-8"))
                os.replace(staging, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(staging, dir_fd=directory)
                raise
        finally:
            os.close(directory)

    @classmethod
    def files(
        cls, root: Path, relative: Relative, pattern: str, recursive: bool = False
    ) -> list[str]:
        """Lists the files below a directory whose names match a pattern, without entering a link.

        Args:
            root: The scratch's root.
            relative: The directory below it.
            pattern: A `fnmatch` pattern for the file's name.
            recursive: Also list the directories below it.

        Returns:
            List[str]: Their paths relative to `root`, sorted (to pass to `open`); empty when the
            directory cannot be read.
        """
        try:
            directory = cls.open_directory(root, cls.parts(relative))
        except (OSError, ValueError):
            return []
        found = []
        try:
            for top, directories, names, _ in os.fwalk(
                ".", dir_fd=directory, follow_symlinks=False
            ):
                found += [
                    str(PurePosixPath(relative, top, name))
                    for name in names
                    if fnmatch.fnmatchcase(name, pattern)
                ]
                if not recursive:
                    directories.clear()
        except OSError:
            pass
        finally:
            os.close(directory)
        return sorted(found)
