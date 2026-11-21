"""Constants for the mestro integration."""

from typing import Final

DOMAIN: Final = "mestro"

CONF_TOKEN: Final = "token"

# the port a player image serves on (no port in its URLs); a desktop run
# uses 8787 and has to be spelled out
DEFAULT_PORT: Final = 80

# the `protocol` value in mestro's `hello` this integration speaks
SUPPORTED_PROTOCOL: Final = 1
