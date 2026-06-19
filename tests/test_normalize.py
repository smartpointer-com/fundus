from fundus.extract.normalize import markdown_to_blocks, text_to_blocks


def test_markdown_headings_and_paragraphs():
    blocks = markdown_to_blocks("# Title\n\nHello world.\n\n## Sub\n\nMore text.")
    levels = {(b.type, b.level) for b in blocks}
    assert ("heading", 1) in levels
    assert ("heading", 2) in levels
    assert any(b.type == "paragraph" and "Hello world." in b.text for b in blocks)


def test_markdown_table_preserved():
    md = "Intro\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    tables = [b for b in markdown_to_blocks(md) if b.type == "table"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert "| a | b |" in tables[0].table.markdown


def test_markdown_code_block():
    blocks = markdown_to_blocks("```\ncode here\n```\n")
    assert any(b.type == "code" and "code here" in b.text for b in blocks)


def test_empty_markdown():
    assert markdown_to_blocks("") == []
    assert markdown_to_blocks("   \n  ") == []


def test_text_to_blocks_splits_paragraphs():
    blocks = text_to_blocks("Para one.\n\nPara two.\n\n\nPara three.")
    assert [b.type for b in blocks] == ["paragraph", "paragraph", "paragraph"]
    assert blocks[2].text == "Para three."


def test_text_to_blocks_empty():
    assert text_to_blocks("  ") == []
