# -*- coding: utf-8 -*-
"""
Handler for the git submodule update command.

Copyright:
    2020 by Clemens Rabe <clemens.rabe@clemensrabe.de>

    All rights reserved.

    This file is part of gitcache (https://github.com/seeraven/gitcache)
    and is released under the "BSD 3-Clause License". Please see the ``LICENSE`` file
    that is included as part of this package.
"""


# -----------------------------------------------------------------------------
# Module Import
# -----------------------------------------------------------------------------
import logging
import os
import re
from typing import List

from ..command_execution import call_command_retry, getstatusoutput, simple_call_command
from ..git_options import GitOptions
from .helpers import get_mirror_url, get_pull_url, resolve_submodule_url

# -----------------------------------------------------------------------------
# Logger
# -----------------------------------------------------------------------------
LOG = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Function Definitions
# -----------------------------------------------------------------------------
# pylint: disable=too-many-locals,too-many-statements,too-many-branches,too-many-nested-blocks
def git_submodule_update(called_as: List[str], git_options: GitOptions) -> int:
    """Handle a git submodule update command.

    A 'git submodule update' command is replaced by calling 'git fetch' or
    'git clone' commands for each submodule using the gitcache wrapper. Then
    the real git command is called to fix the configuration.

    If the option '--init' is given, a 'git submodule init' using the
    gitcache wrapper is performed first.

    Args:
        called_as (list):      The arguments used for the command call.
        git_options (obj):     The GitOptions object.

    Return:
        Returns 0 on success, otherwise the return code of the last failed
        command.
    """
    cd_paths = [path for path in git_options.get_global_group_values("run_path") if path is not None]
    update_paths = git_options.command_args

    # If the --init option is specified, we call 'submodule init' first and
    # remove the option for the following commands. This is exactly the same
    # behaviour as found in https://github.com/git/git/blob/master/git-submodule.sh.
    has_init = "init" in git_options.command_group_values
    has_recursive = "recursive" in git_options.command_group_values
    has_remote = "remote" in git_options.command_group_values
    if has_init:
        command = called_as + git_options.global_options
        command += ["submodule", "init"] + update_paths
        return_value = simple_call_command(command)
        if return_value != 0:
            LOG.error("Initializing submodule with the command %s failed.", command)
            return return_value
        git_options.all_args = [i for i in git_options.all_args if i != "--init"]
        git_options.command_options = [i for i in git_options.command_options if i != "--init"]

    # Make update_paths relative to the checked out repository
    if cd_paths:
        # pylint: disable=no-value-for-parameter
        update_paths = [os.path.relpath(path, os.path.join(*cd_paths)) for path in update_paths]

    command = git_options.get_real_git_with_options()
    command += ["config", "-f", ".gitmodules", "-l"]
    retval, output = getstatusoutput(command)
    if retval == 0:
        pull_url = get_mirror_url(git_options)
        if not pull_url:
            pull_url = get_pull_url(git_options)

        all_keys = [line.split("=")[0] for line in output.split() if "=" in line]
        tgt_url_keys = [key for key in all_keys if key.startswith("submodule") and key.endswith(".url")]
        for tgt_url_key in tgt_url_keys:
            command = git_options.get_real_git_with_options()
            command += ["config", "-f", ".gitmodules", "--get", tgt_url_key]
            retval, tgt_url = getstatusoutput(command)

            if retval != 0:
                continue

            tgt_path_key = tgt_url_key.replace(".url", ".path")
            command = git_options.get_real_git_with_options()
            command += ["config", "-f", ".gitmodules", "--get", tgt_path_key]
            retval, tgt_path = getstatusoutput(command)

            if retval != 0:
                continue

            # Skip not specified target paths unless no path is given at all
            if update_paths and tgt_path not in update_paths:
                continue

            tgt_url = resolve_submodule_url(pull_url, tgt_url)

            abs_tgt_path = os.path.join(*cd_paths, tgt_path)
            if os.path.exists(os.path.join(abs_tgt_path, ".git")):
                # Perform a git fetch in the directory...
                command = called_as + ["fetch"]
                cwd = abs_tgt_path
            else:
                # Perform a git clone into the directory...
                command = called_as + git_options.global_options
                command += ["clone", tgt_url, tgt_path]
                cwd = None

            simple_call_command(command, cwd=cwd)

            # Ensure the checked out repository is on the desired commit.
            command = git_options.get_real_git_with_options()
            command += ["submodule", "update"]
            if has_remote:
                command += ["--remote"]
            command += ["--", tgt_path]
            _, stdout_buffer, stderr_buffer = call_command_retry(command, 0, cwd=cwd)

            # Check for an error message containing a failed fetch of a commit
            failed_hash = None
            if stdout_buffer and b"fetching of that commit failed" in stdout_buffer:
                hashes = re.findall(r"([0-9a-fA-F]{40})", stdout_buffer.decode())
                if hashes:
                    failed_hash = hashes[0]
            if stderr_buffer and b"fetching of that commit failed" in stderr_buffer:
                hashes = re.findall(r"([0-9a-fA-F]{40})", stderr_buffer.decode())
                if hashes:
                    failed_hash = hashes[0]

            if failed_hash:
                # Issue a manual "git fetch origin <commit>" inside the submodule
                command = called_as + ["fetch", "origin", failed_hash]
                if simple_call_command(command, cwd=abs_tgt_path) == 0:
                    command = called_as + ["checkout", failed_hash]
                    if simple_call_command(command, cwd=abs_tgt_path) == 0:
                        command = git_options.get_real_git_with_options()
                        command += ["submodule", "update"]
                        if has_remote:
                            command += ["--remote"]
                        command += ["--", tgt_path]
                        simple_call_command(command, cwd=cwd)

            if has_recursive and os.path.exists(os.path.join(abs_tgt_path, ".gitmodules")):
                command = called_as + ["submodule", "update", "--recursive"]
                if has_init:
                    command.append("--init")
                if has_remote:
                    command.append("--remote")
                simple_call_command(command, cwd=abs_tgt_path)

    return simple_call_command(git_options.get_real_git_all_args())


# -----------------------------------------------------------------------------
# EOF
# -----------------------------------------------------------------------------
