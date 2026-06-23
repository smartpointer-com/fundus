from fundus.core.ids import parent_id
from fundus.core.reconcile import diff_tree, live_parent_ids
from fundus.models import FileStat


class FakeSource:
    name = "mail"
    type = "notmuch"

    def changed(self, cursor):
        return iter([])

    def live_ids(self):
        return iter(["m1", "m2"])

    def current_cursor(self):
        return "0"


def test_live_parent_ids():
    assert live_parent_ids(FakeSource()) == {parent_id("mail", "m1"), parent_id("mail", "m2")}


def test_diff_tree_classifies_new_edited_removed_unchanged():
    indexed = {
        "same": FileStat(10, 100.0),
        "edited_size": FileStat(10, 100.0),
        "edited_mtime": FileStat(10, 100.0),
        "gone": FileStat(10, 100.0),
    }
    disk = {
        "same": FileStat(10, 100.0),  # unchanged
        "edited_size": FileStat(11, 100.0),  # size moved
        "edited_mtime": FileStat(10, 200.0),  # mtime moved
        "new": FileStat(5, 50.0),  # not previously indexed
    }
    changed, removed = diff_tree(disk, indexed)
    assert changed == {"edited_size", "edited_mtime", "new"}
    assert removed == {"gone"}


def test_diff_tree_tolerates_subsecond_mtime_wobble():
    # A JSON round-trip can perturb the mtime float in the last bits; that must not count as a change.
    indexed = {"f": FileStat(10, 100.0)}
    disk = {"f": FileStat(10, 100.0 + 1e-9)}
    changed, removed = diff_tree(disk, indexed)
    assert changed == set() and removed == set()
