from ._version import __version__
from .server import run_server

VERSION = __version__
__all__ = ["run_server", "VERSION", "__version__"]
