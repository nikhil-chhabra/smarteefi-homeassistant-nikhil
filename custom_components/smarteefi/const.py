import os
import platform

architecture = platform.machine().lower()  # Convert to lowercase for case-insensitive comparison

if architecture in ("x86_64", "amd64"):
    arch = "x86-64bit"
elif architecture in ("i386", "i686", "x86"):
    arch = "x86-32bit"
elif architecture in ("aarch64", "arm64"):
    arch = "arm-64bit"
elif "armv" in architecture:  # ARM 32-bit (e.g., armv7l, armv6l)
    arch = "arm-32bit"
else:
    arch = "unknown"  # Default for unsupported or unknown architectures

system = platform.system()
if system == 'Windows':
    os_name = 'win'
elif system == 'Linux':
    os_name = 'linux'
elif system == 'Darwin':
    os_name = 'mac'
else:
    os_name = 'unknown'


DOMAIN = 'smarteefi'
ARCH = arch
OS = os_name

PLATFORMS = ["switch", "fan", "light", "cover"]

# Full path to the HACLI binary bundled with this integration
HACLI_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    f"hacli-{OS}-{ARCH}",
    "smarteefi-ha-cli.exe" if OS == 'win' else "smarteefi-ha-cli",
)

UDP_PORT = 8890  # Port devices push status updates to

CLI_TIMEOUT = 10  # Seconds before a HACLI call is abandoned

INITIAL_SYNC_INTERVAL = 5 # Sync interval for the first sync after HA Restart
SYNC_INTERVAL = 3 # Sync interval in seconds
