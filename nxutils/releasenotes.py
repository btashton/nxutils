import re
import time
from datetime import datetime, timedelta

import requests

from PyInquirer import prompt, Separator
from rich.table import Table
from rich.columns import Columns
from rich.progress import track
from rich.text import Text
from rich.panel import Panel
from rich.padding import Padding
from rich.console import RenderGroup
from rich.markdown import Markdown
from rich.syntax import Syntax

from nxutils import console

PROJECT_COLUMNS = ["To-Add", "In Progress", "Added", "Minor", "Not Applicable"]


def editprompt(gh, repo):
    existing_projects = [
        dict(name=proj.name, value=proj) for proj in repo.get_projects(state="all")
    ]

    select_project = {
        "type": "list",
        "name": "project",
        "message": "Which project?",
        "choices": existing_projects
        + [
            Separator(),
            "New Project",
        ],
    }

    project = prompt(select_project)["project"]
    if project == "New Project":
        console.print("Creating a new Project")
        question = {
            "type": "input",
            "name": "version",
            "message": "What release version",
        }
        version = prompt(question)["version"]
        project = newproject(gh, repo, version)

    question = {
        "type": "list",
        "name": "action",
        "message": "What would you like to do",
        "choices": [
            {
                "name": "Sync new pull requests",
                "value": lambda: syncprs(gh, repo, project),
            },
            {
                "name": "Triage existing PRs",
                "value": lambda: triage_proj(gh, repo, project),
            },
            Separator(),
            {
                "name": "Done",
                "value": lambda: None,
            },
        ],
    }
    action = prompt(question)["action"]
    return action()


def newproject(gh, repo, version):
    proj = repo.create_project(f"Release Notes - {version}")
    for column in PROJECT_COLUMNS:
        proj.create_column(column)
    return proj


def syncprs(gh, repo, proj):
    console.print(Panel("Sync PRs to the release note board"))
    console.print(
        Padding(
            Text(
                "We need to find a date range to use for finding PRs that might have made it into the release.\n"
                "Here are when release branches were made as well as recent tags."
            ),
            (1, 0),
        )
    )
    branch_table = branchtable(repo)
    tag_table = tagtable(repo)
    console.print(Padding(Columns([branch_table, tag_table]), (1, 0)))

    console.print("Date times should be in the GitHub format as listed here:")
    console.print(
        "\thttps://docs.github.com/en/free-pro-team@latest/github/searching-for-information-on-github/understanding-the-search-syntax"
    )
    console.print("Examples\n\tYYYY-MM-DD\n\tYYYY-MM-DDTHH:MM:SS+00:00")
    console.print("For ANY use '*'")

    questions = [
        {
            "type": "input",
            "name": "start",
            "message": "PR range start",
        },
        {
            "type": "input",
            "name": "end",
            "message": "PR range end",
        },
    ]
    answer = prompt(questions)
    console.print(
        Padding(
            Text(
                f'Query repo:{repo.full_name} is:pr is:merged merged:{answer["start"]}..{answer["end"]}',
                style="bold yellow",
            ),
            (1, 4),
        )
    )

    query = f'repo:{repo.full_name} is:pr is:merged merged:{answer["start"]}..{answer["end"]}'
    console.print(query)
    issues = gh.search_issues(query=query)
    console.print(f"Found {issues.totalCount} PRs that could be part of this")

    prs = {}
    for pr in track(issues, description="Processing PRs", total=issues.totalCount):
        prs[pr.number] = pr

    pr_nums = set(prs.keys())

    # We should query all the other release boards to see if the PRs are active there.
    existing_pr_nums = set()
    projs = repo.get_projects(state="all")
    for proj in projs:
        console.print(f"Loading board {proj.name}...")
        if re.match("Release Notes -", proj.name) is None:
            console.print(f"Skipping board {proj.name}")
            continue
        for col in proj.get_columns():
            if col.name == "Not Applicable":
                # The card is not really part of this release board
                continue
            cards = col.get_cards()
            for card in cards:
                match = re.match(r".*/issues/(\d+)", card.content_url)
                if match is None:
                    continue

                pr_id = int(match.groups()[0])
                existing_pr_nums.add(pr_id)

    of_interest = sorted(pr_nums.difference(existing_pr_nums))
    console.print(f"Need to sort {len(of_interest)} PRs")

    question = {
        "type": "confirm",
        "name": "bulk_add",
        "message": "Bulk add all PRs",
        "default": False,
    }
    bulk_add = prompt(question)["bulk_add"]

    if bulk_add:
        to_add_col = None
        for col in proj.get_columns():
            if col.name == "To-Add":
                to_add_col = col
                break

        if to_add_col is None:
            raise Exception("To-Add column missing from project")

        for pr_id in track(
            of_interest, description="Creating cards", total=len(of_interest)
        ):
            pr = prs[pr_id].as_pull_request()
            to_add_col.create_card(content_id=pr.id, content_type="PullRequest")

    question = {
        "type": "confirm",
        "name": "triage",
        "message": "Triage board cards",
        "default": True,
    }
    triage_cards = prompt(question)["triage"]

    if triage_cards:
        triage_proj(gh, repo, proj)


def triage_proj(gh, repo, proj):
    to_add_col = None
    columns = {col.name: col for col in proj.get_columns()}
    to_add_col = columns["To-Add"]

    cards = to_add_col.get_cards()
    console.clear()
    pos = 1
    for idx, card in enumerate(cards):
        console.print(f"Card {idx+1} of {cards.totalCount}...")
        if triage_card(columns, card) == False:
            return
        console.clear()
    console.print("No more cards.")


def triage_card(columns, card):

    content = card.get_content()
    pr = content.as_pull_request()
    render_pr(pr)

    question = {
        "type": "list",
        "name": "option",
        "message": "What would you like to do",
        "choices": [
            "Show Diff",
            "Skip",
            "Quit",
        ]
        + PROJECT_COLUMNS,
    }

    while True:
        try:
            option = prompt(question)["option"]
            if option == "Show Diff":
                # Rich now has a pager we should include once released
                with console.pager(styles=True):
                    diff = requests.get(pr.diff_url).content.decode()
                    console.print(Syntax(diff, "diff"))
            elif option == "Skip":
                return True
            elif option in PROJECT_COLUMNS:
                card.move("bottom", columns[option])
                return True
            elif option == "Quit":
                return False
            else:
                raise Exception(f"Did not expect {option}")
        except KeyError:
            return False


def render_pr(pr):
    labels = ", ".join([label.name for label in pr.get_labels()])
    body = Markdown(pr.body)
    pr_panel = Panel(
        RenderGroup(
            Panel(f"Title: {pr.title}"),
            Panel(f"Labels: {labels}"),
            Panel(body, title="Body"),
        ),
        title=f"PR #{pr.number}",
    )

    console.print(pr_panel)


def branchtable(repo):
    table = Table(
        title="Branches Updated in Last Year",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Branch")
    table.add_column("Branched Point from Master SHA")
    table.add_column("Branched from Master Commit Date")

    branches = repo.get_branches()
    for branch in track(
        branches, description="Fetching branches...", total=branches.totalCount
    ):
        if branch.name != "master" and re.match("releases/", branch.name) is None:
            continue

        commit = branch.commit
        if branch.name != "master":
            comparison = repo.compare("master", branch.commit.sha)
            commit = repo.get_commit(f"{branch.commit.sha}~{comparison.ahead_by}")

        commiter_date = commit.commit.committer.date

        # commiter_date = branch.commit.commit.committer.date
        commiter_date.replace(microsecond=0)

        # only reporting branches from the last year
        if (datetime.now() - commiter_date) > timedelta(days=365):
            continue

        table.add_row(
            branch.name,
            commit.sha,
            commiter_date.isoformat(),
        )
    return table


def tagtable(repo):
    table = Table(
        title="Tags Updated in Last Year",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Tag")
    table.add_column("Commited")
    tags = repo.get_tags()
    for tag in track(tags, description="Fetching tags...", total=tags.totalCount):
        # We are going to limit this to the new format releases
        # Making queries for the commit date is very slow and we would need
        # to move to the graphql API to make this fast
        if re.match("nuttx-\d.\d.\d", tag.name) is None:
            continue

        commiter_date = tag.commit.commit.committer.date
        commiter_date.replace(microsecond=0)

        # only reporting tags from the last year
        if (datetime.now() - commiter_date) > timedelta(days=365):
            continue

        table.add_row(
            tag.name,
            commiter_date.isoformat(),
        )
    return table
