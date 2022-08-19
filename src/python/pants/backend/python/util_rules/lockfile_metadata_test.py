# Copyright 2021 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import itertools
from typing import Iterable

import pytest

from pants.backend.python.pip_requirement import PipRequirement
from pants.backend.python.util_rules.interpreter_constraints import InterpreterConstraints
from pants.backend.python.util_rules.lockfile_metadata import (
    InvalidPythonLockfileReason,
    PythonLockfileMetadata,
    PythonLockfileMetadataV1,
    PythonLockfileMetadataV2,
    PythonLockfileMetadataV3,
)
from pants.core.util_rules.lockfile_metadata import calculate_invalidation_digest

INTERPRETER_UNIVERSE = ["2.7", "3.5", "3.6", "3.7", "3.8", "3.9", "3.10"]


def reqset(*a) -> set[PipRequirement]:
    return {PipRequirement.parse(i) for i in a}


def test_metadata_header_round_trip() -> None:
    input_metadata = PythonLockfileMetadata.new(
        valid_for_interpreter_constraints=InterpreterConstraints(
            ["CPython==2.7.*", "PyPy", "CPython>=3.6,<4,!=3.7.*"]
        ),
        requirements=reqset("ansicolors==0.1.0"),
        manylinux="manylinux2014",
        requirement_constraints={PipRequirement.parse("constraint")},
        only_binary={PipRequirement.parse("bdist")},
        no_binary={PipRequirement.parse("sdist")},
    )
    serialized_lockfile = input_metadata.add_header_to_lockfile(
        b"req1==1.0", regenerate_command="./pants lock", delimeter="#"
    )
    output_metadata = PythonLockfileMetadata.from_lockfile(
        serialized_lockfile, resolve_name="a", delimeter="#"
    )
    assert input_metadata == output_metadata


def test_add_header_to_lockfile() -> None:
    input_lockfile = b"""dave==3.1.4 \\
    --hash=sha256:cab0c0c0c0c0dadacafec0c0c0c0cafedadabeefc0c0c0c0feedbeeffeedbeef \\
    """

    expected = b"""
# This lockfile was autogenerated by Pants. To regenerate, run:
#
#    ./pants lock
#
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 3,
#   "valid_for_interpreter_constraints": [
#     "CPython>=3.7"
#   ],
#   "generated_with_requirements": [
#     "ansicolors==0.1.0"
#   ],
#   "manylinux": null,
#   "requirement_constraints": [
#     "constraint"
#   ],
#   "only_binary": [
#     "bdist"
#   ],
#   "no_binary": [
#     "sdist"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---
dave==3.1.4 \\
    --hash=sha256:cab0c0c0c0c0dadacafec0c0c0c0cafedadabeefc0c0c0c0feedbeeffeedbeef \\
    """

    def line_by_line(b: bytes) -> list[bytes]:
        return [i for i in (j.strip() for j in b.splitlines()) if i]

    metadata = PythonLockfileMetadata.new(
        valid_for_interpreter_constraints=InterpreterConstraints([">=3.7"]),
        requirements=reqset("ansicolors==0.1.0"),
        manylinux=None,
        requirement_constraints={PipRequirement.parse("constraint")},
        only_binary={PipRequirement.parse("bdist")},
        no_binary={PipRequirement.parse("sdist")},
    )
    result = metadata.add_header_to_lockfile(
        input_lockfile, regenerate_command="./pants lock", delimeter="#"
    )
    assert line_by_line(result) == line_by_line(expected)


def test_invalidation_digest() -> None:
    a = "flake8-pantsbuild>=2.0,<3"
    b = "flake8-2020>=1.6.0,<1.7.0"
    c = "flake8"

    def assert_eq(left: Iterable[str], right: Iterable[str]) -> None:
        assert calculate_invalidation_digest(left) == calculate_invalidation_digest(right)

    def assert_neq(left: Iterable[str], right: Iterable[str]) -> None:
        assert calculate_invalidation_digest(left) != calculate_invalidation_digest(right)

    for reqs in itertools.permutations([a, b, c]):
        assert_eq(reqs, [a, b, c])
        assert_neq(reqs, [a, b])

    assert_eq([], [])
    assert_neq([], [a])
    assert_eq([a, a, a, a], [a])


@pytest.mark.parametrize(
    "user_digest, expected_digest, user_ic, expected_ic, matches",
    [
        (
            "yes",
            "yes",
            [">=3.5.5"],
            [">=3.5, <=3.6"],
            False,
        ),  # User ICs contain versions in the 3.7 range
        ("yes", "yes", [">=3.5.5, <=3.5.10"], [">=3.5, <=3.6"], True),
        ("yes", "no", [">=3.5.5, <=3.5.10"], [">=3.5, <=3.6"], False),  # Digests do not match
        (
            "yes",
            "yes",
            [">=3.5.5, <=3.5.10"],
            [">=3.5", "<=3.6"],
            True,
        ),  # User ICs match each of the actual ICs individually
        (
            "yes",
            "yes",
            [">=3.5.5, <=3.5.10"],
            [">=3.5", "<=3.5.4"],
            True,
        ),  # User ICs do not match one of the individual ICs
        ("yes", "yes", ["==3.5.*, !=3.5.10"], [">=3.5, <=3.6"], True),
        (
            "yes",
            "yes",
            ["==3.5.*"],
            [">=3.5, <=3.6, !=3.5.10"],
            False,
        ),  # Excluded IC from expected range is valid for user ICs
        ("yes", "yes", [">=3.5, <=3.6", ">= 3.8"], [">=3.5"], True),
        (
            "yes",
            "yes",
            [">=3.5, <=3.6", ">= 3.8"],
            [">=3.5, !=3.7.10"],
            True,
        ),  # Excluded version from expected ICs is not in a range specified
    ],
)
def test_is_valid_for_v1(user_digest, expected_digest, user_ic, expected_ic, matches) -> None:
    m: PythonLockfileMetadata
    m = PythonLockfileMetadataV1(InterpreterConstraints(expected_ic), expected_digest)
    assert (
        bool(
            m.is_valid_for(
                is_tool=True,
                expected_invalidation_digest=user_digest,
                user_interpreter_constraints=InterpreterConstraints(user_ic),
                interpreter_universe=INTERPRETER_UNIVERSE,
                user_requirements=set(),
                manylinux=None,
                requirement_constraints=set(),
                only_binary=set(),
                no_binary=set(),
            )
        )
        == matches
    )


_VALID_ICS = [">=3.5"]
_VALID_REQS = ["ansicolors==0.1.0", "requests==1.0.0"]

# Different scenarios that are the same for both tool lockfiles and user lockfiles.
_LockfileConditions = (
    [_VALID_ICS, _VALID_ICS, _VALID_REQS, _VALID_REQS, []],
    [_VALID_ICS, _VALID_ICS, _VALID_REQS, list(reversed(_VALID_REQS)), []],
    [
        _VALID_ICS,
        _VALID_ICS,
        _VALID_REQS,
        [_VALID_REQS[0], "requests==2.0.0"],
        [InvalidPythonLockfileReason.REQUIREMENTS_MISMATCH],
    ],
    [
        _VALID_ICS,
        _VALID_ICS,
        _VALID_REQS,
        [_VALID_REQS[0], "different"],
        [InvalidPythonLockfileReason.REQUIREMENTS_MISMATCH],
    ],
    [
        _VALID_ICS,
        _VALID_ICS,
        _VALID_REQS,
        [*_VALID_REQS, "a-third-req"],
        [InvalidPythonLockfileReason.REQUIREMENTS_MISMATCH],
    ],
    [
        _VALID_ICS,
        ["==2.7.*"],
        _VALID_REQS,
        _VALID_REQS,
        [InvalidPythonLockfileReason.INTERPRETER_CONSTRAINTS_MISMATCH],
    ],
)


@pytest.mark.parametrize(
    "is_tool, lock_ics, user_ics, lock_reqs, user_reqs, expected",
    [
        *([True, *conditions] for conditions in _LockfileConditions),
        *([False, *conditions] for conditions in _LockfileConditions),
        # Tools require exact matches, whereas user lockfiles only need to subset.
        [False, _VALID_ICS, _VALID_ICS, _VALID_REQS, [_VALID_REQS[0]], []],
        [
            True,
            _VALID_ICS,
            _VALID_ICS,
            _VALID_REQS,
            [_VALID_REQS[0]],
            [InvalidPythonLockfileReason.REQUIREMENTS_MISMATCH],
        ],
    ],
)
def test_is_valid_for_interpreter_constraints_and_requirements(
    is_tool: bool,
    user_ics: list[str],
    lock_ics: list[str],
    user_reqs: list[str],
    lock_reqs: list[str],
    expected: list[InvalidPythonLockfileReason],
) -> None:
    """This logic is used by V2 and newer."""
    for m in [
        PythonLockfileMetadataV2(InterpreterConstraints(lock_ics), reqset(*lock_reqs)),
        PythonLockfileMetadataV3(
            InterpreterConstraints(lock_ics),
            reqset(*lock_reqs),
            manylinux=None,
            requirement_constraints=set(),
            only_binary=set(),
            no_binary=set(),
        ),
    ]:
        result = m.is_valid_for(
            is_tool=is_tool,
            expected_invalidation_digest="",
            user_interpreter_constraints=InterpreterConstraints(user_ics),
            interpreter_universe=INTERPRETER_UNIVERSE,
            user_requirements=reqset(*user_reqs),
            manylinux=None,
            requirement_constraints=set(),
            only_binary=set(),
            no_binary=set(),
        )
        assert result.failure_reasons == set(expected)


@pytest.mark.parametrize("is_tool", [True, False])
def test_is_valid_for_v3_metadata(is_tool: bool) -> None:
    result = PythonLockfileMetadataV3(
        InterpreterConstraints([]),
        reqset(),
        # Everything below is new to v3+.
        manylinux=None,
        requirement_constraints={PipRequirement.parse("c1")},
        only_binary={PipRequirement.parse("bdist")},
        no_binary={PipRequirement.parse("sdist")},
    ).is_valid_for(
        is_tool=is_tool,
        expected_invalidation_digest="",
        user_interpreter_constraints=InterpreterConstraints([]),
        interpreter_universe=INTERPRETER_UNIVERSE,
        user_requirements=reqset(),
        manylinux="manylinux2014",
        requirement_constraints={PipRequirement.parse("c2")},
        only_binary={PipRequirement.parse("not-bdist")},
        no_binary={PipRequirement.parse("not-sdist")},
    )
    assert result.failure_reasons == {
        InvalidPythonLockfileReason.CONSTRAINTS_FILE_MISMATCH,
        InvalidPythonLockfileReason.ONLY_BINARY_MISMATCH,
        InvalidPythonLockfileReason.NO_BINARY_MISMATCH,
        InvalidPythonLockfileReason.MANYLINUX_MISMATCH,
    }
