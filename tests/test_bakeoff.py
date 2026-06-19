from fundus.bakeoff.runner import run_bakeoff
from fundus.models import Block, DocMeta, EngineRef, ExtractionResult


class FakeExtractor:
    version = "1"

    def __init__(self, name, fail=False):
        self.name = name
        self._fail = fail

    def extract(self, req):
        if self._fail:
            raise RuntimeError("boom")
        return ExtractionResult(
            engine=EngineRef(name=self.name, version="1"),
            blocks=[Block(type="paragraph", text="hi"), Block(type="table", text="t")],
            markdown="hi\n\nt",
            metadata=DocMeta(),
        )


def test_bakeoff_collects_metrics_and_survives_failures(tmp_path):
    sample = tmp_path / "a.txt"
    sample.write_text("hello")
    out = tmp_path / "out"
    results = run_bakeoff(
        [sample], [FakeExtractor("good"), FakeExtractor("bad", fail=True)], out_dir=out
    )
    assert len(results) == 1
    runs = {r.engine: r for r in results[0].runs}
    assert runs["good"].ok and runs["good"].table_count == 1
    assert not runs["bad"].ok and runs["bad"].error
    assert (out / "good" / "a.md").exists()
