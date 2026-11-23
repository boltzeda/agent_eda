"""Product updates must fetch from the release repository, not a clone's origin."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'distribution' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import update_product


class ProductUpdateSourceTests(unittest.TestCase):
    def test_fetch_uses_official_release_independent_of_origin(self):
        product = Path('C:/agent-eda')
        with patch.object(update_product, 'run') as run:
            update_product.fetch_release(product)
        run.assert_called_once_with(
            'git', '-C', str(product), 'fetch', '--no-tags',
            'https://github.com/boltzeda/agent_eda.git', 'main')


if __name__ == '__main__':
    unittest.main()
