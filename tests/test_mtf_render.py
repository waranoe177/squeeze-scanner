"""The multi-timeframe table image (spec §5)."""

from scanner import mtf, mtf_render

B1, B3 = mtf.Cell("B", 1, True), mtf.Cell("B", 3, False)
S1, S2 = mtf.Cell("S", 1, False), mtf.Cell("S", 2, False)
E = mtf.Cell(None, 0, False)


def _rows(n):
    return [mtf.Row(f"T{i:03d}", (B1, B3, E, E)) for i in range(n)]


def test_cell_style():
    assert mtf_render.cell_style(B1) == ("* B: 1", mtf_render.GREEN, mtf_render.FIRST_BG)
    assert mtf_render.cell_style(B3) == ("B: 3", mtf_render.GREEN, None)
    assert mtf_render.cell_style(S2) == ("S: 2", mtf_render.RED, None)
    assert mtf_render.cell_style(E) == ("—", mtf_render.MUTED, None)


def test_header_text():
    rows = [mtf.Row("A", (B1, B3, E, E)), mtf.Row("B", (S1, S2, E, E))]
    assert mtf_render.header_text("2026-10-09", rows) == (
        "Sqzdots MTF · v5+REV · Fri 2026-10-09 · 2 tickers (1 buy · 1 sell)")
    rows.append(mtf.Row("C", (B1, B3, S1, S2)))
    assert mtf_render.header_text("2026-10-09", rows).endswith(
        "3 tickers (1 buy · 1 sell · 1 mixed)")
    assert mtf_render.header_text("2026-10-09", rows[:1]).endswith("1 ticker (1 buy · 0 sell)")


def test_caption_and_fixed_strings():
    assert mtf_render.caption_text(_rows(23)) == "Multi-timeframe: 23 tickers on 2+ timeframes"
    assert mtf_render.QUIET == "No ticker has a signal on 2+ timeframes today."
    assert mtf_render.FOOTER == (
        "* = squeeze · B/S: n = bars the signal has held · gray = started this bar")


def test_render_table_one_page(tmp_path):
    paths = mtf_render.render_table(_rows(3), "2026-10-09", tmp_path)
    assert [p.name for p in paths] == ["mtf_table.png"]
    assert paths[0].stat().st_size > 0


def test_render_table_splits_at_40(tmp_path):
    paths = mtf_render.render_table(_rows(85), "2026-10-09", tmp_path)
    assert [p.name for p in paths] == ["mtf_table.png", "mtf_table_2.png", "mtf_table_3.png"]


def test_render_table_removes_stale_pages(tmp_path):
    mtf_render.render_table(_rows(85), "2026-10-08", tmp_path)
    mtf_render.render_table(_rows(5), "2026-10-09", tmp_path)
    assert sorted(p.name for p in tmp_path.glob("mtf_table*.png")) == ["mtf_table.png"]


def test_render_table_no_rows_writes_nothing(tmp_path):
    (tmp_path / "mtf_table.png").write_bytes(b"old")
    assert mtf_render.render_table([], "2026-10-09", tmp_path) == []
    assert not list(tmp_path.glob("mtf_table*.png"))
