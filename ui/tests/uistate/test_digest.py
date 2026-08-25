"""The drift guard: it must localise a change, not merely notice one.

Deliberately built from duck-typed stubs rather than real Kivy widgets. The
digest reads plain attributes (class name, geometry, opacity, text, children),
so stubs exercise exactly the logic under test -- and building real ``Label``s
under the mock GL backend segfaults in Kivy's canvas/texture path. Real widgets
are covered by the ``render``-marked replay test, which has a real GL context.
"""

from reflex.uistate import digest


class W:
    """A widget as far as the digest is concerned."""

    def __init__(self, cls, text="", w=100, h=40, x=0, y=0, opacity=1,
                 children=()):
        self.__class__ = type(cls, (W,), {})
        self.text = text
        self.width, self.height = w, h
        self.x, self.y = x, y
        self.opacity = opacity
        self.disabled = False
        self.children = list(children)


class FakeApp:
    def __init__(self, root):
        self.root = root


def build():
    bar = W("ElsBar", w=1024, h=100, children=[W("Button", text="Cut")])
    adv = W("ElsAdvancedBar", w=1024, h=128,
            children=[W("Label", text="Ready to cut", w=300)])
    root = W("HomePage", w=1024, h=600, children=[bar, adv])
    return FakeApp(root), bar, adv


def test_digest_is_stable_across_two_walks():
    app, _bar, _adv = build()
    assert digest.subtree_digests(app) == digest.subtree_digests(app)


def test_regions_are_reported_separately():
    app, _bar, _adv = build()
    assert {"ElsBar", "ElsAdvancedBar", "root", "all"} <= set(
        digest.subtree_digests(app))


def test_a_text_change_moves_only_its_own_region():
    """This is what makes a drift report say WHERE, not just THAT."""
    app, _bar, adv = build()
    before = digest.subtree_digests(app)
    adv.children[0].text = "Cutting..."
    after = digest.subtree_digests(app)

    assert digest.compare(before, after) == ["ElsAdvancedBar"]
    assert after["ElsBar"] == before["ElsBar"]
    assert after["all"] != before["all"]


def test_a_geometry_change_is_detected():
    app, bar, _adv = build()
    before = digest.subtree_digests(app)
    bar.children[0].width = 250
    assert digest.compare(before, digest.subtree_digests(app)) == ["ElsBar"]


def test_hiding_a_widget_is_detected():
    """The UI hides things by collapsing to zero height, so the walk must too."""
    app, _bar, adv = build()
    before = digest.subtree_digests(app)
    adv.height = 0
    assert "ElsAdvancedBar (missing at replay)" in digest.compare(
        before, digest.subtree_digests(app))


def test_zero_opacity_subtree_is_treated_as_absent():
    app, _bar, adv = build()
    adv.opacity = 0
    assert "ElsAdvancedBar" not in digest.subtree_digests(app)


def test_identical_trees_compare_clean():
    app_a, _b, _c = build()
    app_b, _d, _e = build()
    assert digest.compare(digest.subtree_digests(app_a),
                          digest.subtree_digests(app_b)) == []


def test_subpixel_geometry_is_not_reported_as_drift():
    """Pi vs dev-box rasterisation differs; that is not a missing field."""
    app, bar, _adv = build()
    before = digest.subtree_digests(app)
    bar.x += 0.001
    assert digest.compare(before, digest.subtree_digests(app)) == []


def test_digest_never_raises_on_a_broken_tree():
    """A guard that can take the UI down is worse than no guard."""
    class Exploding:
        @property
        def root(self):
            raise RuntimeError("boom")

    digests = digest.subtree_digests(Exploding())
    assert set(digests) == {"all"}


def test_describe_tree_lists_regions():
    app, _bar, _adv = build()
    lines = digest.describe_tree(app)
    assert any(line.startswith("ElsAdvancedBar\t") for line in lines)
    assert any("Ready to cut" in line for line in lines)
