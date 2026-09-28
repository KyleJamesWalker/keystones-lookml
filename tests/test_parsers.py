"""The lkml-backed parser: definitions, comments and a label-blind rendering."""

import pytest
from keystones.parser import Unparseable

from keystones_lookml.parsers import lookml

VIEW = """# top comment
view: orders {
  sql_table_name: analytics.orders ;;
  # keystone: total
  measure: total {
    type: sum
    sql: ${TABLE}.amount ;;   # trailing
    description: "Sum of amounts"
    label: "Total"
    filters: [status: "paid", region: "us"]
  }

  dimension: id {
    primary_key: yes
    type: number
    sql: ${TABLE}.id ;;
  }
}

explore: orders {
  join: users {
    sql_on: ${orders.user_id} = ${users.id} ;;
  }
}
"""


@pytest.fixture
def parser():
    return lookml()


def test_identity_names_the_library_version(parser):
    from importlib.metadata import version

    assert parser.name == "lookml"
    assert parser.identity == f"lkml@{version('lkml')}"


def test_drop_must_be_names():
    with pytest.raises(ValueError):
        lookml(drop="label")


def test_blocks_are_definitions_with_line_spans(parser):
    defs = parser.parse(VIEW).definitions()
    assert [(d.qualname, d.start, d.end) for d in defs] == [
        ("view.orders", 2, 18),
        ("view.orders.measure.total", 5, 11),
        ("view.orders.dimension.id", 13, 17),
        ("explore.orders", 20, 24),
        ("explore.orders.join.users", 21, 23),
    ]


def test_comments_come_with_their_lines(parser):
    assert parser.parse(VIEW).comments() == [
        (1, "# top comment"),
        (4, "# keystone: total"),
        (7, "# trailing"),
    ]


def rendered(parser, src: str, qualname: str) -> str:
    tree = parser.parse(src)
    [definition] = [d for d in tree.definitions() if d.qualname == qualname]
    return tree.render(definition)


def test_description_and_label_are_not_in_the_rendering(parser):
    before = rendered(parser, VIEW, "view.orders.measure.total")
    edited = VIEW.replace('description: "Sum of amounts"', 'description: "Amounts"')
    edited = edited.replace('label: "Total"', 'label: "Total spend"')
    assert rendered(parser, edited, "view.orders.measure.total") == before


def test_a_sql_change_is_in_the_rendering(parser):
    before = rendered(parser, VIEW, "view.orders.measure.total")
    edited = VIEW.replace("${TABLE}.amount", "${TABLE}.net_amount")
    assert rendered(parser, edited, "view.orders.measure.total") != before


def test_reordering_parameters_is_not_a_change(parser):
    before = rendered(parser, VIEW, "view.orders.measure.total")
    edited = VIEW.replace(
        "    type: sum\n    sql: ${TABLE}.amount ;;   # trailing\n",
        "    sql: ${TABLE}.amount ;;   # trailing\n    type: sum\n",
    )
    assert rendered(parser, edited, "view.orders.measure.total") == before


def test_whitespace_inside_an_expression_is_not_a_change(parser):
    before = rendered(parser, VIEW, "explore.orders.join.users")
    edited = VIEW.replace(
        "sql_on: ${orders.user_id} = ${users.id} ;;",
        "sql_on:\n      ${orders.user_id}   =\n      ${users.id} ;;",
    )
    assert rendered(parser, edited, "explore.orders.join.users") == before


def test_list_order_is_a_change(parser):
    before = rendered(parser, VIEW, "view.orders.measure.total")
    edited = VIEW.replace(
        '[status: "paid", region: "us"]', '[region: "us", status: "paid"]'
    )
    assert rendered(parser, edited, "view.orders.measure.total") != before


def test_the_drop_list_is_configurable():
    parser = lookml(drop=["label"])
    before = rendered(parser, VIEW, "view.orders.measure.total")
    edited = VIEW.replace('description: "Sum of amounts"', 'description: "Amounts"')
    assert rendered(parser, edited, "view.orders.measure.total") != before


def test_a_fragment_renders_like_the_block_in_context(parser):
    """C5 re-hashes the stored slice on its own; this is what makes that hold."""
    lines = VIEW.splitlines()[4:11]
    import textwrap

    fragment = textwrap.dedent("\n".join(lines)) + "\n"
    tree = parser.parse_fragment(fragment)
    [definition] = tree.definitions()
    assert tree.render(definition) == rendered(
        parser, VIEW, "view.orders.measure.total"
    )


def test_the_whole_document_renders_without_labels(parser):
    before = parser.parse(VIEW).render(None)
    edited = VIEW.replace('label: "Total"', 'label: "Spend"')
    assert parser.parse(edited).render(None) == before
    assert parser.parse(VIEW.replace("type: sum", "type: count")).render(None) != before


def test_bad_lookml_is_unparseable(parser):
    with pytest.raises(Unparseable):
        parser.parse("view: orders {\n  measure: total {\n")
