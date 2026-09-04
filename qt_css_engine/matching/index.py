"""StyleIndex — quick filters, flags, buckets, ancestor relevance.

Precomputed lookup structures over the rule set used by rule matching.
"""

from dataclasses import dataclass, field

from qt_css_engine.matching.compiler import WidgetIdentity


@dataclass
class QuickFilter:
    tags: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)


@dataclass
class Flags:
    has_descendant: bool = False
    has_effect: bool = False
    has_border_radius: bool = False


@dataclass
class Buckets:
    by_id: dict[str, list[int]] = field(default_factory=dict)
    by_tag: dict[str, list[int]] = field(default_factory=dict)
    by_class: dict[str, list[int]] = field(default_factory=dict)
    unconditional: list[int] = field(default_factory=list)


@dataclass
class AncestorFilter:
    tags: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)

    def is_relevant(self, ident: WidgetIdentity) -> bool:
        return ident.obj_name in self.ids or ident.tag in self.tags or not self.classes.isdisjoint(ident.classes)


@dataclass
class StyleIndex:
    quick: QuickFilter = field(default_factory=QuickFilter)
    flags: Flags = field(default_factory=Flags)
    buckets: Buckets = field(default_factory=Buckets)
    ancestor: AncestorFilter = field(default_factory=AncestorFilter)

    def clear(self) -> None:
        self.quick.tags.clear()
        self.quick.classes.clear()
        self.quick.ids.clear()
        self.flags.has_descendant = False
        self.flags.has_effect = False
        self.flags.has_border_radius = False
        self.buckets.by_id.clear()
        self.buckets.by_tag.clear()
        self.buckets.by_class.clear()
        self.buckets.unconditional.clear()
        self.ancestor.tags.clear()
        self.ancestor.classes.clear()
        self.ancestor.ids.clear()
