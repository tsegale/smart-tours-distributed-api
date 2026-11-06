"""Model files at a pinned revision, checked against a pinned hash before anything loads them (ADR 0020).

The revision stops upstream's next commit from arriving unnoticed. The hash covers what a revision can't: a rewritten
repository, or a damaged or altered file in the local cache. Quality measurements only hold for the bytes measured.
"""

import hashlib
import logging

log = logging.getLogger(__name__)


class ModelFileChangedError(RuntimeError):
    """The bytes on disk aren't the ones the evaluation measured. Refuse to run on them."""


def sha256_of(path: str) -> str:
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def pinned_file(repo: str, filename: str, revision: str, sha256: str | None) -> str:
    """The local path of a verified file. sha256 is None only for candidates built on the fly by the CPU survey."""
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(repo, filename, revision=revision)
    if sha256 is not None:
        actual = sha256_of(path)
        if actual != sha256:
            raise ModelFileChangedError(
                f"{repo}/{filename} at {revision} has sha256 {actual}, expected {sha256}. "
                "If this change is intended, re-run the evaluation and the CPU survey before updating the pin."
            )
    return path
