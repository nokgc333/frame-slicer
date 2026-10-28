"""Names vulture cannot see being used: Click registers the commands, urllib calls the handler."""

from frame_slicer.cli import split, submit
from frame_slicer.transport import _NoRedirect

split, submit
_NoRedirect.redirect_request
