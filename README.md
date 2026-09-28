# keystones-lookml

Gate a LookML view, explore or field on what it means, not on its labels.

A [keystones](https://github.com/KyleJamesWalker/keystones) keystone pins a
review gate to an AST node. LookML has no tree-sitter grammar, and its
`description` and `label` parameters churn constantly, so a text region trips
on every wording change. This parser plugin reads LookML with
[lkml](https://github.com/joshtemple/lkml) and drops those parameters from the
hash.

## Install

```bash
pip install keystones-lookml
```

```toml
[[tool.keystones.language]]
extensions = [".lkml"]
parser = "keystones_lookml.parsers:lookml"
```

`.lkml` matches `orders.view.lkml` and `orders.model.lkml` by suffix. To drop
other parameters as well, or fewer:

```toml
parser = { plugin = "keystones_lookml.parsers:lookml", drop = ["label", "description", "group_label"] }
```

The drop list is part of the hasher, so changing it is a `keystones migrate`
rather than drift.

## What a keystone covers

Every block is a definition, named by type and label under its parents:

```lookml
view: orders {
  # keystone(bi): total-spend
  measure: total {
    type: sum
    sql: ${TABLE}.amount ;;
    label: "Total"
  }
}
```

That keystone is `orders.view.lkml::view.orders.measure.total`. A marker above
`view: orders {` covers the whole view; `keystone(file)` covers the file.
Explores and joins work the same way: `explore.orders.join.users`.

## What counts as a change

| Edit | Result |
|---|---|
| `sql`, `type`, `filters`, any parameter not dropped | C3, the owner reviews |
| `description` or `label` | nothing |
| reordering parameters inside a block | nothing |
| whitespace inside a `sql` expression | nothing |
| reordering items in a list such as `filters` | C3, lists are ordered |
| a comment line inside the block | C4, `fix` clears it with no note |

## License

MIT
