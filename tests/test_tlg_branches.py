"""Branch management checks without external APIs or a production database."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

class HttpError(Exception):
    def __init__(self, status, detail):
        self.status_code = status
        super().__init__(detail)

names = {'normalized', 'centre_name', 'validate_branch_name', 'create_centre', 'rename_centre'}
tree = ast.parse(Path('backend/routes/tlg.py').read_text())
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
for node in nodes:
    node.decorator_list = []
ns = dict(HTTPException=HttpError, CENTRES=['Whitefield', 'Yelahanka'], Depends=lambda value: None,
          get_db=None, get_current_user_required=None, Session=object, BranchName=object)
ns.update(TlgCentre=lambda **kw: SimpleNamespace(id=10, **kw), access=Mock(), serial=lambda row: vars(row))
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'branches', 'exec'), ns)

class BranchTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.query.return_value.filter_by.return_value.all.return_value = [SimpleNamespace(id=1, name='Whitefield')]
        ns['access'].reset_mock(side_effect=True)

    def test_create_normalizes_name_and_keeps_server_sync_disabled(self):
        result = ns['create_centre'](7, SimpleNamespace(name='  New   Branch '), self.db, object())
        self.assertEqual(result['name'], 'New Branch')
        self.assertFalse(result['enabled'])
        ns['access'].assert_called_once()
        self.db.commit.assert_called_once()

    def test_duplicate_and_blank_names_rejected(self):
        for name in [' WHITEFIELD ', '  ', '---']:
            with self.assertRaises(HttpError):
                ns['validate_branch_name'](name, 7, self.db)

    def test_rename_preserves_links_and_settings(self):
        row = SimpleNamespace(id=1, name='Whitefield', source_url='master', destination_url='calling', enabled=True)
        self.db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = row
        result = ns['rename_centre'](7, 1, SimpleNamespace(name='Whitefield KAI'), self.db, object())
        self.assertEqual(result['name'], 'Whitefield KAI')
        self.assertEqual((row.source_url, row.destination_url, row.enabled), ('master', 'calling', True))

    def test_unauthorized_creation_cannot_write(self):
        ns['access'].side_effect = HttpError(403, 'Not authorized')
        with self.assertRaises(HttpError):
            ns['create_centre'](7, SimpleNamespace(name='New'), self.db, object())
        self.db.add.assert_not_called()

    def test_new_branch_campaign_mapping(self):
        self.assertEqual(ns['centre_name']('TLG_NewBranch_Leads', ['New Branch']), 'New Branch')
        self.assertEqual(ns['centre_name']('Unknown', ['New Branch']), 'Unmapped')

if __name__ == '__main__':
    unittest.main()
