"""Compatibility alias for the laboratory app views."""
import sys
from apps.laboratory import views
sys.modules[__name__] = views
