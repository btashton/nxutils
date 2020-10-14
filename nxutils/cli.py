import re
import click

from github import Github
from PyInquirer import prompt, Separator

from .releasenotes import editprompt, render_pr, triage_proj


@click.group()
def cli():
    pass


@cli.command()
@click.option("--github-token")
@click.option("--repo", default="apache/incubator-nuttx")
def releasenotes(github_token, repo):
    gh = Github(github_token)
    gh_repo = gh.get_repo(repo)
    editprompt(gh, gh_repo)


@cli.command()
@click.option("--github-token")
@click.option("--repo", default="apache/incubator-nuttx")
def triage(github_token, repo):
    gh = Github(github_token)
    gh_repo = gh.get_repo(repo)

    projects = []
    for proj in gh_repo.get_projects(state="all"):
        if re.match("Release Notes -", proj.name) is None:
            continue
        projects.append(proj)

    existing_projects = [dict(name=proj.name, value=proj) for proj in projects]

    select_project = {
        "type": "list",
        "name": "project",
        "message": "Which project?",
        "choices": existing_projects,
    }
    triage_proj(gh, gh_repo, prompt(select_project)["project"])
