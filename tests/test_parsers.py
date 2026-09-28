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
    from lkml.tree import BlockNode

    tree = parser.parse(VIEW)
    blocks = [d for d, node in tree._defs if isinstance(node, BlockNode)]
    defs = sorted(blocks, key=lambda d: (d.start, d.qualname))
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
    definition = tree.definitions()[0]
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


# --- repeated unnamed blocks, parameters, list drops, spacing -------------------

CASES = """view: orders {
  dimension: tier {
    case: {
      when: {
        sql: ${TABLE}.amount > 100 ;;
        label: "big"
      }
      when: {
        sql: ${TABLE}.amount > 10 ;;
        label: "mid"
      }
      else: "small"
    }
    tags: ["a", "b"]
  }
}

explore: orders {
  sql_always_where: ${orders.deleted} = false ;;
  join: users {
    sql_on: ${orders.user_id} = ${users.id} ;;
  }
}
"""


def test_repeated_unnamed_siblings_get_positional_qualnames(parser):
    names = [d.qualname for d in parser.parse(CASES).definitions()]
    assert "view.orders.dimension.tier.case.when[0]" in names
    assert "view.orders.dimension.tier.case.when[1]" in names
    assert "view.orders.dimension.tier.case.when" not in names
    assert "view.orders.dimension.tier.case" in names, "a single block keeps its name"


def test_the_second_when_is_its_own_target(parser):
    second = "view.orders.dimension.tier.case.when[1]"
    before = rendered(parser, CASES, second)
    edited = CASES.replace("${TABLE}.amount > 10", "${TABLE}.amount > 20")
    assert rendered(parser, edited, second) != before
    edited = CASES.replace("${TABLE}.amount > 100", "${TABLE}.amount > 200")
    assert rendered(parser, edited, second) == before, "the first when is not mine"


def test_a_parameter_is_addressable(parser):
    defs = {d.qualname: d for d in parser.parse(CASES).definitions()}
    where = defs["explore.orders.sql_always_where"]
    assert (where.start, where.end) == (19, 19)
    before = parser.parse(CASES).render(where)
    edited = CASES.replace("${orders.deleted} = false", "${orders.deleted} = true")
    after = parser.parse(edited)
    assert after.render(defs["explore.orders.sql_always_where"]) != before


def test_a_parameter_fragment_renders_like_the_parameter_in_context(parser):
    import textwrap

    line = CASES.splitlines()[18]
    tree = parser.parse_fragment(textwrap.dedent(line) + "\n")
    [definition] = tree.definitions()
    expected = rendered(parser, CASES, "explore.orders.sql_always_where")
    assert tree.render(definition) == expected


def test_drop_applies_to_lists():
    parser = lookml(drop=["tags"])
    before = rendered(parser, CASES, "view.orders.dimension.tier")
    edited = CASES.replace('tags: ["a", "b"]', 'tags: ["a"]')
    assert rendered(parser, edited, "view.orders.dimension.tier") == before


def test_spacing_between_list_items_is_not_a_change(parser):
    before = rendered(parser, CASES, "view.orders.dimension.tier")
    edited = CASES.replace('tags: ["a", "b"]', 'tags: ["a","b"]')
    assert rendered(parser, edited, "view.orders.dimension.tier") == before
