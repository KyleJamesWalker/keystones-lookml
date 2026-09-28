"""Drive a LookML view through keystones with the plugin configured."""

import subprocess
from pathlib import Path

import pytest

PYPROJECT = """[tool.keystones]
root = "keystones"
categories = ["default", "bi"]

[[tool.keystones.language]]
extensions = [".lkml"]
parser = "keystones_lookml.parsers:lookml"
"""

VIEW = """view: orders {
  sql_table_name: analytics.orders ;;

  # keystone(bi): total-spend
  measure: total {
    type: sum
    sql: ${TABLE}.amount ;;
    description: "Sum of amounts"
    label: "Total"
  }

  dimension: id {
    primary_key: yes
    type: number
    sql: ${TABLE}.id ;;
  }
}
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    github = tmp_path / ".github"
    github.mkdir()
    (github / "CODEOWNERS").write_text(
        "/keystones/          @org/bi\n"
        "/pyproject.toml      @org/bi\n"
        "/.github/CODEOWNERS  @org/bi\n"
    )
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "orders.view.lkml").write_text(VIEW)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def run(project: Path, monkeypatch):
    from keystones.cli import main

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    def _run(*args: str) -> int:
        return main(["--repo-root", str(project), *args])

    return _run


def view(project: Path) -> Path:
    return project / "views" / "orders.view.lkml"


def sidecar(project: Path) -> str:
    return (project / "keystones" / "bi" / "total-spend.md").read_text()


def test_a_measure_resolves_to_a_node(project, run):
    assert run("add", "--id", "total-spend", "-m", "Revenue definition.") == 0
    text = sidecar(project)
    assert 'target = "views/orders.view.lkml::view.orders.measure.total"' in text
    assert 'hash = "lookml"' in text
    assert "keystones-plugin/1+lkml@" in text
    assert "sql: ${TABLE}.amount ;;" in text, "the stored source is the block"


def test_a_fresh_keystone_passes_every_check(project, run):
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    assert run("check", "--all", "--no-base") == 0


def test_a_sql_change_gates(project, run, capsys):
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    view(project).write_text(VIEW.replace("${TABLE}.amount", "${TABLE}.net"))
    assert run("check", "--all", "--no-base") == 1
    assert "[C3]" in capsys.readouterr().err


def test_label_and_description_churn_does_not_gate(project, run):
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    view(project).write_text(
        VIEW.replace('description: "Sum of amounts"', 'description: "Spend"').replace(
            'label: "Total"', 'label: "Total spend"'
        )
    )
    assert run("check", "--all", "--no-base") == 0


def test_a_comment_edit_inside_the_block_is_c4(project, run, capsys):
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    view(project).write_text(
        VIEW.replace("    type: sum\n", "    # in cents\n    type: sum\n")
    )
    assert run("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C4]" in err and "[C3]" not in err


def test_editing_another_field_does_not_gate(project, run):
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    view(project).write_text(VIEW.replace("type: number", "type: string"))
    assert run("check", "--all", "--no-base") == 0


def test_the_stored_source_proves_itself(project, run, capsys):
    """C5: the slice stored in the sidecar re-renders to the stored hash."""
    run("add", "--id", "total-spend", "-m", "Revenue definition.")
    path = project / "keystones" / "bi" / "total-spend.md"
    path.write_text(path.read_text().replace("${TABLE}.amount", "${TABLE}.other"))
    assert run("check", "--all", "--no-base") == 1
    assert "[C5]" in capsys.readouterr().err
