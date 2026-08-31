import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import daily_arxiv


def make_row(day, title, paper_id):
    return (
        f"|**{day}**|**{title}**|cs.CL|Abstract|"
        f"[{paper_id}](https://arxiv.org/abs/{paper_id})|null|\n"
    )


class ReadmeRenderingTests(unittest.TestCase):
    def test_readme_contains_only_newest_100_papers_from_2017(self):
        papers = {}
        first_day = datetime.date(2025, 1, 1)
        for index in range(105):
            paper_id = f"2501.{index:05d}"
            day = first_day + datetime.timedelta(days=index)
            papers[paper_id] = make_row(day, f"paper-{index}", paper_id)

        # An old submission with a newer update date must still be hidden.
        papers["1612.00001"] = make_row(
            datetime.date(2025, 12, 31), "old-modern", "1612.00001"
        )
        papers["cmp-lg/9505039"] = make_row(
            datetime.date(2008, 2, 3), "old-legacy", "cmp-lg/9505039"
        )
        archive = {"task oriented dialogue": papers}

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "papers.json"
            readme_path = Path(temp_dir) / "README.md"
            json_path.write_text(json.dumps(archive), encoding="utf-8")

            daily_arxiv.json_to_md(
                json_path,
                readme_path,
                use_tc=False,
                show_badge=False,
            )

            rendered = readme_path.read_text(encoding="utf-8")
            rows = [line for line in rendered.splitlines() if line.startswith("|**")]
            self.assertEqual(100, len(rows))
            self.assertIn("paper-104", rows[0])
            self.assertNotIn("**paper-4**", rendered)
            self.assertNotIn("old-modern", rendered)
            self.assertNotIn("old-legacy", rendered)

            # Rendering must not remove the hidden papers from the JSON archive.
            self.assertEqual(archive, json.loads(json_path.read_text(encoding="utf-8")))


class ArxivClientTests(unittest.TestCase):
    @mock.patch("daily_arxiv.get_official_code_url", return_value=None)
    @mock.patch("daily_arxiv.arxiv.Client")
    def test_fetch_uses_client_results_api(self, client_type, _code_lookup):
        result = SimpleNamespace(
            categories=["cs.CL"],
            title="A task-oriented dialogue paper",
            entry_id="https://arxiv.org/abs/2501.00001v1",
            summary="An abstract.",
            updated=datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc),
            get_short_id=lambda: "2501.00001v1",
        )
        client = client_type.return_value
        client.results.return_value = [result]

        data, _ = daily_arxiv.get_daily_papers("topic", "ti:test", max_results=1)

        client.results.assert_called_once()
        self.assertIn("2501.00001", data["topic"])


class ArchiveMergeTests(unittest.TestCase):
    def test_existing_code_link_survives_optional_api_failure(self):
        stored = make_row(datetime.date(2025, 1, 1), "paper", "2501.00001").replace(
            "|null|\n", "|**[link](https://github.com/example/repo)**|\n"
        )
        refreshed = make_row(datetime.date(2025, 2, 1), "paper", "2501.00001")

        merged = daily_arxiv.merge_paper_rows(stored, refreshed)

        self.assertIn("2025-02-01", merged)
        self.assertIn("https://github.com/example/repo", merged)

    @mock.patch(
        "daily_arxiv.requests.get",
        side_effect=daily_arxiv.requests.ConnectionError("service unavailable"),
    )
    def test_optional_code_api_failure_opens_circuit(self, request_get):
        daily_arxiv._code_lookup_available = True
        try:
            self.assertIsNone(daily_arxiv.get_official_code_url("2501.00001v1"))
            self.assertIsNone(daily_arxiv.get_official_code_url("2501.00002v1"))
            request_get.assert_called_once()
        finally:
            daily_arxiv._code_lookup_available = True


if __name__ == "__main__":
    unittest.main()
