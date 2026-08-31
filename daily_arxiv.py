import datetime
import json
import re

import arxiv
import requests


PAPERS_WITH_CODE_API = "https://arxiv.paperswithcode.com/api/v0/papers/"
README_MAX_PAPERS = 100
README_MIN_YEAR = 2017
ROW_DATE_PATTERN = re.compile(r"^\|\*\*(\d{4}-\d{2}-\d{2})\*\*\|")
MODERN_ARXIV_ID_PATTERN = re.compile(r"^(\d{2})\d{2}\.\d+$")
LEGACY_ARXIV_ID_PATTERN = re.compile(r"^[^/]+/(\d{2})\d+$")
_code_lookup_available = True


def get_label(categories):
    return ", ".join(str(category) for category in categories)


def get_row_date(row):
    """Return the date embedded in a stored Markdown row."""
    match = ROW_DATE_PATTERN.match(row or "")
    if not match:
        return datetime.date.min
    try:
        return datetime.date.fromisoformat(match.group(1))
    except ValueError:
        return datetime.date.min


def get_submission_year(paper_id, row=""):
    """Infer an arXiv submission year, falling back to the stored row date."""
    unversioned_id = re.sub(r"v\d+$", "", paper_id)

    match = MODERN_ARXIV_ID_PATTERN.match(unversioned_id)
    if match:
        return 2000 + int(match.group(1))

    match = LEGACY_ARXIV_ID_PATTERN.match(unversioned_id)
    if match:
        year = int(match.group(1))
        return 1900 + year if year >= 90 else 2000 + year

    row_date = get_row_date(row)
    return row_date.year if row_date != datetime.date.min else None


def select_readme_papers(data, max_papers=README_MAX_PAPERS, min_year=README_MIN_YEAR):
    """Select the newest papers for README while leaving the JSON archive intact."""
    candidates = []
    for keyword, papers in data.items():
        for paper_id, row in papers.items():
            if row is None:
                continue
            submission_year = get_submission_year(paper_id, row)
            if submission_year is None or submission_year < min_year:
                continue
            candidates.append((get_row_date(row), paper_id, keyword, row))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    selected = {keyword: [] for keyword in data}
    for _, paper_id, keyword, row in candidates[:max_papers]:
        selected[keyword].append((paper_id, row))
    return selected


def get_official_code_url(paper_id):
    """Return the Papers with Code repository URL when one is available."""
    global _code_lookup_available
    if not _code_lookup_available:
        return None

    try:
        response = requests.get(PAPERS_WITH_CODE_API + paper_id, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Could not query Papers with Code for {paper_id}: {exc}")
        # Do not let a retired or temporarily unavailable optional API delay every paper.
        _code_lookup_available = False
        return None

    official = payload.get("official")
    return official.get("url") if official else None


def markdown_cell(value):
    return str(value).replace("\n", " ").replace("|", "\\|")


def get_daily_papers(topic, query, max_results=2):
    """Fetch recent arXiv papers for a topic."""
    content = {}
    content_to_web = {}

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )
    client = arxiv.Client(page_size=min(max_results, 100), delay_seconds=3, num_retries=3)

    # Search.results() is deprecated and was absent in some arxiv releases.
    for result in client.results(search):
        if (
            "cs.CL" not in result.categories
            or "cs.CV" in result.categories
            or "eess.AS" in result.categories
            or "cs.SD" in result.categories
            or "eess.SP" in result.categories
        ):
            continue

        paper_id = result.get_short_id()
        paper_key = re.sub(r"v\d+$", "", paper_id)
        paper_title = markdown_cell(result.title)
        paper_url = result.entry_id
        paper_categories = markdown_cell(get_label(result.categories))
        paper_abstract = markdown_cell(result.summary)
        update_time = result.updated.date()

        print(
            "Time =",
            update_time,
            "title =",
            result.title,
            "categories =",
            paper_categories,
        )

        repo_url = get_official_code_url(paper_id)
        code_cell = f"**[link]({repo_url})**" if repo_url else "null"
        content[paper_key] = (
            f"|**{update_time}**|**{paper_title}**|{paper_categories}|{paper_abstract} "
            f"|[{paper_id}]({paper_url})|{code_cell}|\n"
        )

        web_entry = (
            f"- {update_time}, **{paper_title}**,{paper_categories}, {paper_abstract}, "
            f"Paper: [{paper_url}]({paper_url})"
        )
        if repo_url:
            web_entry += f", Code: **[{repo_url}]({repo_url})**"
        content_to_web[paper_key] = web_entry + "\n"

    return {topic: content}, {topic: content_to_web}


def update_json_file(filename, data_all):
    with open(filename, "r", encoding="utf-8") as file:
        content = file.read()
        json_data = json.loads(content) if len(content.strip()) >= 2 else {}

    for data in data_all:
        for keyword, papers in data.items():
            stored_papers = json_data.setdefault(keyword, {})
            for paper_id, row in papers.items():
                stored_papers[paper_id] = merge_paper_rows(stored_papers.get(paper_id), row)

    with open(filename, "w", encoding="utf-8") as file:
        json.dump(json_data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def merge_paper_rows(stored_row, new_row):
    """Keep a known code link when the optional code lookup is unavailable."""
    if not stored_row or not new_row:
        return new_row

    stored_parts = stored_row.rstrip("\n").rsplit("|", 2)
    new_parts = new_row.rstrip("\n").rsplit("|", 2)
    if len(stored_parts) != 3 or len(new_parts) != 3:
        return new_row

    stored_code = stored_parts[1]
    new_code = new_parts[1]
    if new_code == "null" and stored_code != "null":
        return f"{new_parts[0]}|{stored_code}|\n"
    return new_row


def json_to_md(
    filename,
    md_filename,
    to_web=False,
    use_title=True,
    use_tc=True,
    show_badge=True,
    max_papers=README_MAX_PAPERS,
    min_year=README_MIN_YEAR,
):
    """Render a bounded, recent subset of the complete JSON archive as Markdown."""
    date_now = datetime.date.today().strftime("%Y.%m.%d")

    with open(filename, "r", encoding="utf-8") as file:
        content = file.read()
        data = json.loads(content) if content else {}

    selected = select_readme_papers(data, max_papers=max_papers, min_year=min_year)

    with open(md_filename, "w", encoding="utf-8") as file:
        if use_title and to_web:
            file.write("---\nlayout: default\n---\n\n")

        if show_badge:
            file.write("[![Contributors][contributors-shield]][contributors-url]\n")
            file.write("[![Forks][forks-shield]][forks-url]\n")
            file.write("[![Stargazers][stars-shield]][stars-url]\n")
            file.write("[![Issues][issues-shield]][issues-url]\n\n")

        if use_title:
            file.write(f"## Updated on {date_now}\n\n")
        else:
            file.write(f"> Updated on {date_now}\n\n")

        archive_link = "arxiv-daily.json" if to_web else "docs/arxiv-daily.json"
        file.write(
            f"> Showing the {max_papers} newest papers submitted in {min_year} or later. "
            f"The complete archive is stored in [{archive_link}]({archive_link}).\n\n"
        )

        if use_tc:
            file.write("<details>\n")
            file.write("  <summary>Table of Contents</summary>\n")
            file.write("  <ol>\n")
            for keyword, papers in selected.items():
                if papers:
                    anchor = keyword.replace(" ", "-")
                    file.write(f"    <li><a href=#{anchor}>{keyword}</a></li>\n")
            file.write("  </ol>\n")
            file.write("</details>\n\n")

        for keyword, papers in selected.items():
            if not papers:
                continue

            file.write(f"## {keyword}\n\n")
            if use_title:
                if to_web:
                    file.write("| Date | Title | label | Abstract | PDF | Code |\n")
                    file.write("|:---------|:---------------|:-------|:------------------|:------|:------|\n")
                else:
                    file.write("|Date|Title|label|Abstract|PDF|Code|\n")
                    file.write("|---|---|---|---|---|---|\n")

            for _, row in papers:
                file.write(row)

            file.write("\n")
            top_info = f"#Updated-on-{date_now}".replace(".", "")
            file.write(f"<p align=right>(<a href={top_info}>back to top</a>)</p>\n\n")

        if show_badge:
            file.write(
                "[contributors-shield]: https://img.shields.io/github/contributors/"
                "bansky-cl/tods-arxiv-daily-paper.svg?style=for-the-badge\n"
            )
            file.write(
                "[contributors-url]: https://github.com/bansky-cl/"
                "tods-arxiv-daily-paper/graphs/contributors\n"
            )
            file.write(
                "[forks-shield]: https://img.shields.io/github/forks/"
                "bansky-cl/tods-arxiv-daily-paper.svg?style=for-the-badge\n"
            )
            file.write(
                "[forks-url]: https://github.com/bansky-cl/"
                "tods-arxiv-daily-paper/network/members\n"
            )
            file.write(
                "[stars-shield]: https://img.shields.io/github/stars/"
                "bansky-cl/tods-arxiv-daily-paper.svg?style=for-the-badge\n"
            )
            file.write(
                "[stars-url]: https://github.com/bansky-cl/"
                "tods-arxiv-daily-paper/stargazers\n"
            )
            file.write(
                "[issues-shield]: https://img.shields.io/github/issues/"
                "bansky-cl/tods-arxiv-daily-paper.svg?style=for-the-badge\n"
            )
            file.write(
                "[issues-url]: https://github.com/bansky-cl/"
                "tods-arxiv-daily-paper/issues\n"
            )

    print(
        f"Rendered {sum(len(papers) for papers in selected.values())} papers "
        f"from {min_year} onward to {md_filename}"
    )


def main():
    data_collector = []
    keywords = {"task oriented dialogue": 'ti:"task oriented dialogue"'}

    for topic, keyword in keywords.items():
        print("Keyword: " + topic)
        data, _ = get_daily_papers(topic, query=keyword, max_results=50)
        data_collector.append(data)

    json_file = "docs/arxiv-daily.json"
    md_file = "README.md"
    update_json_file(json_file, data_collector)
    json_to_md(json_file, md_file)


if __name__ == "__main__":
    main()
