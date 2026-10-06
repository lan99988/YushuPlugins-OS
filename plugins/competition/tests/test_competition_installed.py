"""Independent provider installation regression."""
from tests import test_domain_lifecycles as checks

def test_competition_installed(tmp_path):
    checks.test_independent_core_installed_domain_roundtrip_and_invalid_input(tmp_path, 'competition')
