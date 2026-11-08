"""Send Qt diagnostics to the test log instead of the Windows debug channel."""
import sys
from PySide6.QtCore import qInstallMessageHandler

_seen_messages = set()


def _qt_message(kind, context, message):
    if message not in _seen_messages:
        _seen_messages.add(message)
        print(f"Qt {kind.name}: {message}", file=sys.stderr, flush=True)


qInstallMessageHandler(_qt_message)
