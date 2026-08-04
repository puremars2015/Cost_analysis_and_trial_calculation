import unittest
from unittest.mock import patch, MagicMock

from app import app, BOM_ITEM_URL, ORG_OPTIONS


SAMPLE_DATA = [
    {
        "item_9": "93.00058.200",
        "item_9_id": 6627,
        "base_weight": "58",
        "item_5": "53.00058.L60",
        "resource_rate": 1.57,
        "item_3": ["36.SLG3X.150"],
        "item_3_count": 1,
    }
]


def _mock_ok(data=None):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"status": "S", "message": None, "data": data if data is not None else []}
    return m


def _mock_error(message="找不到料號"):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"status": "E", "message": message, "data": []}
    return m


class TestBomItemParams(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def _get_params(self, url):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok()
            self.client.get(url)
            return mock_get.call_args.kwargs.get("params", {})

    def test_item_no_sent_when_provided(self):
        params = self._get_params("/?org_code=WPN&item_no=93.00058.200")
        self.assertEqual(params.get("item_no"), "93.00058.200")
        self.assertEqual(params.get("org_code"), "WPN")

    def test_empty_item_no_not_sent(self):
        params = self._get_params("/?org_code=WPN&item_no=")
        self.assertNotIn("item_no", params)

    def test_whitespace_item_no_not_sent(self):
        params = self._get_params("/?org_code=WPN&item_no=   ")
        self.assertNotIn("item_no", params)

    def test_stripped_item_no_sent(self):
        params = self._get_params("/?org_code=WPN&item_no=+93.00058.200+")
        self.assertEqual(params.get("item_no"), "93.00058.200")


class TestOrgOptions(unittest.TestCase):
    def test_no_all_option(self):
        self.assertNotIn("ALL", ORG_OPTIONS)

    def test_three_org_codes(self):
        self.assertIn("WPN", ORG_OPTIONS)
        self.assertIn("WPT", ORG_OPTIONS)
        self.assertIn("WPD", ORG_OPTIONS)
        self.assertEqual(len(ORG_OPTIONS), 3)

    def test_select_renders_three_options(self):
        app.testing = True
        client = app.test_client()
        response = client.get("/")
        body = response.data.decode()
        self.assertIn("WPN", body)
        self.assertIn("WPT", body)
        self.assertIn("WPD", body)
        self.assertNotIn(">ALL<", body)


class TestErrorHandling(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_api_status_not_s_shows_error_message(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_error("找不到料號")
            response = self.client.get("/?org_code=WPN&item_no=99.INVALID")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode()
        self.assertIn("API 回傳錯誤", body)
        self.assertIn("找不到料號", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("Exception", body)

    def test_api_timeout_shows_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.Timeout()):
            response = self.client.get("/?org_code=WPN")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode()
        self.assertIn("逾時", body)
        self.assertNotIn("Traceback", body)

    def test_non_json_response_shows_error(self):
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.side_effect = ValueError("no JSON")
        with patch("requests.get", return_value=m):
            response = self.client.get("/?org_code=WPN")
        self.assertEqual(response.status_code, 200)
        self.assertIn("非 JSON", response.data.decode())


class TestExcelDownload(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_excel_download_sends_correct_params(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok(SAMPLE_DATA)
            response = self.client.get("/?org_code=WPT&item_no=93.00058.200&export=1")
        params = mock_get.call_args.kwargs.get("params", {})
        self.assertEqual(params.get("org_code"), "WPT")
        self.assertEqual(params.get("item_no"), "93.00058.200")

    def test_excel_download_content_type(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok(SAMPLE_DATA)
            response = self.client.get("/?org_code=WPT&item_no=93.00058.200&export=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response.content_type)

    def test_excel_download_no_item_no_not_in_params(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok(SAMPLE_DATA)
            response = self.client.get("/?org_code=WPT&export=1")
        params = mock_get.call_args.kwargs.get("params", {})
        self.assertNotIn("item_no", params)
        self.assertEqual(params.get("org_code"), "WPT")

    def test_excel_download_error_falls_back_to_html(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_error()
            response = self.client.get("/?org_code=WPT&item_no=BAD&export=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.content_type)
        self.assertIn("API 回傳錯誤", response.data.decode())


class TestDataRendering(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_item_3_list_joined_in_table(self):
        data = [{
            "item_9": "93.00001.000",
            "item_5": "53.00001.A00",
            "base_weight": "100",
            "resource_rate": 2.0,
            "item_3": ["36.MAT1.100", "36.MAT2.200"],
            "item_3_count": 2,
        }]
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok(data)
            response = self.client.get("/?org_code=WPN")
        body = response.data.decode()
        self.assertIn("36.MAT1.100", body)
        self.assertIn("36.MAT2.200", body)

    def test_no_data_shows_empty_message(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_ok([])
            response = self.client.get("/?org_code=WPN")
        self.assertIn("查無", response.data.decode())


if __name__ == "__main__":
    unittest.main(verbosity=2)
