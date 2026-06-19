from fundus.core.ids import parent_id
from fundus.core.reconcile import live_parent_ids


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
