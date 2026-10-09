"""Pure routing checks: no database or external service access."""
import ast
import unittest
from unittest.mock import patch, mock_open
from pathlib import Path

source = Path("backend/services/tlg.py").read_text()
tree = ast.parse(source)
nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.ImportFrom) and n.module.startswith("backend")) or isinstance(n, ast.FunctionDef) and n.name in ("sheet_ref", "column", "plan_rows", "sheets_identity") or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MARKER" for t in n.targets)]
ns = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "routing", "exec"), ns)
plan = ns["plan_rows"]

class RoutingTests(unittest.TestCase):
    def test_shared_sheets_credential_reused(self):
        with patch.dict(ns["os"].environ, {"CRASH_CLUB_GOOGLE_SA_JSON": '{"client_email":"shared@example.test"}'}, clear=True):
            self.assertEqual(ns["sheets_identity"]()["client_email"], "shared@example.test")

    def test_existing_sheets_file_reused(self):
        with patch.dict(ns["os"].environ, {"CRASH_CLUB_GOOGLE_SA_FILE": "/configured/account.json"}, clear=True), patch.object(ns["os"].path, "isfile", return_value=True), patch("builtins.open", mock_open(read_data='{"client_email":"file@example.test"}')) as opened:
            self.assertEqual(ns["sheets_identity"]()["client_email"], "file@example.test")
            opened.assert_called_once_with("/configured/account.json", encoding="utf-8")

    def test_missing_sheets_credential_explains_setup(self):
        with patch.dict(ns["os"].environ, {}, clear=True), patch.object(ns["os"].path, "isfile", return_value=False):
            with self.assertRaisesRegex(ValueError, "Google Sheets is not connected"):
                ns["sheets_identity"]()

    def test_duplicate_submissions_and_backfill(self):
        headers, rows = plan([["Name"], ["Test"], ["Test"]], [], "master:0")
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][-1], rows[1][-1])
        self.assertEqual(plan([["Name"], ["Test"], ["Test"]], [headers]+rows, "master:0")[1], [])

    def test_preserves_branch_notes_and_maps_headers(self):
        headers, rows = plan([["Name", "Phone"], ["New", "123"]], [["Remarks", "Phone", "Name", ns["MARKER"]], ["Called", "456", "Old", "master:0:1"]], "master:0")
        self.assertEqual(rows[0], ["", "123", "New", "master:0:2"])

    def test_blank_rows_do_not_shift_identity(self):
        _, rows = plan([["Name"], [], ["Test"]], [], "master:0")
        self.assertEqual(rows[0][-1], "master:0:3")

    def test_invalid_headers_stop_sync(self):
        with self.assertRaises(ValueError):
            plan([["Name", "Name"], ["a", "b"]], [], "master:0")

    def test_existing_remark_header(self):
        headers, _ = plan([["Name", "Remarks"], ["a", ""]], [], "master:0")
        self.assertEqual(headers.count("Remarks"), 1)

    def test_sheet_tab_and_url_validation(self):
        self.assertEqual(ns["sheet_ref"]("https://docs.google.com/spreadsheets/d/abc/edit#gid=12"), ("abc", 12))
        with self.assertRaises(ValueError):
            ns["sheet_ref"]("https://example.com/spreadsheets/d/abc")

if __name__ == "__main__":
    unittest.main()
