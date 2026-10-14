import asyncio
import logging
import os
import stat

from .const import CLI_TIMEOUT

_LOGGER = logging.getLogger(__name__)


async def run_hacli(hacli_path, args, timeout=CLI_TIMEOUT):
    """Run the HACLI binary and return its stripped stdout, or None on failure."""
    command_str = " ".join([hacli_path, *args])
    _LOGGER.debug(f"Executing CLI command: {command_str}")

    try:
        process = await asyncio.create_subprocess_exec(
            hacli_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        _LOGGER.error(f"CLI binary not found at {hacli_path}")
        return None
    except Exception as e:
        _LOGGER.error(f"Error executing CLI: {e}")
        return None

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        _LOGGER.error(f"Command timed out after {timeout}s: {command_str}")
        process.kill()
        await process.wait()
        return None
    except asyncio.CancelledError:
        process.kill()
        raise

    if process.returncode != 0:
        _LOGGER.error(f"Command failed: {command_str}, Error: {stderr.decode(errors='replace').strip()}")
        return None

    output = stdout.decode(errors="replace").strip()
    _LOGGER.debug(f"Command succeeded: {command_str}, Output: {output}")
    return output


def set_executable_permissions(file_path: str) -> bool:
    """Set executable permissions on the HACLI binary. Blocking: run in an executor."""
    if not os.path.exists(file_path):
        _LOGGER.error(f"CLI not found at {file_path}")
        return False
    current_perms = os.stat(file_path).st_mode
    os.chmod(file_path, current_perms | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True
