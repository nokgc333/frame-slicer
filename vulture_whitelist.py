"""Names vulture cannot see being used: Click registers the commands, urllib calls the handler."""

from frame_slicer.cli import commands, retry, split, status, submit
from frame_slicer.transport import _NoRedirect

split, submit, retry, status, commands
_NoRedirect.redirect_request
