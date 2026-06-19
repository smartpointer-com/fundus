from fundus.core.ids import content_sha256, doc_id, options_hash, parent_id
from fundus.extract.base import ExtractOptions


def test_content_sha256_deterministic():
    assert content_sha256(b"abc") == content_sha256(b"abc")
    assert content_sha256(b"abc") != content_sha256(b"abd")


def test_doc_id_stable_and_seq_varies():
    a = doc_id("mail", "msg-1", 0)
    assert a == doc_id("mail", "msg-1", 0)
    assert a != doc_id("mail", "msg-1", 1)
    assert a != doc_id("chat", "msg-1", 0)


def test_blake_domain_separation():
    # ("a","b") must not collide with ("ab",)
    assert parent_id("a", "b") != parent_id("ab", "")


def test_parent_id_independent_of_seq():
    assert parent_id("mail", "msg-1") == parent_id("mail", "msg-1")
    assert parent_id("mail", "msg-1") != parent_id("mail", "msg-2")


def test_options_hash_order_independent():
    a = options_hash(ExtractOptions(ocr="auto", ocr_languages=["eng", "deu"]))
    b = options_hash(ExtractOptions(ocr_languages=["eng", "deu"], ocr="auto"))
    assert a == b
    assert a != options_hash(ExtractOptions(ocr="force", ocr_languages=["eng", "deu"]))
