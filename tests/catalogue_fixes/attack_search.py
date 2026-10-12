"""Attack tests for B3/D6a (camelCase split), D6b (plural fold), B4 (schema stopwords), old index.

Beyond tests/catalogue_fixes/test_search_split_stem_stop.py. Run by file name (the name does not
match pytest's default `test_*.py`):

    uv run pytest tests/catalogue_fixes/attack_search.py -p no:cacheprovider

A failing test here is a defect found in the implementation; none is marked xfail. Names are
invented and generic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import SearchRequest, index_snapshot, search

ROOT = Path(__file__).resolve().parents[2]


def _state(tmp_path, monkeypatch, tables, name="s"):
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name=name)
    index_snapshot(snapshot, tmp_path / f"state-{name}")
    return tmp_path / f"state-{name}"


def _names(state, query, **kwargs):
    return [i["name"] for i in search(state, SearchRequest(query=query, **kwargs))["results"]]


def _fields(*names):
    return {"t": {"fields": [(n, "text") for n in names]}}


# --- B3/D6a: names with digits, mixed case, unicode ----------------------------------------------


@pytest.mark.parametrize(
    ("name", "query"),
    [
        ("addressLine2", "line"),
        ("addressLine2", "address"),
        ("v2Id", "id"),
        ("PascalCaseName", "case"),
        ("PascalCaseName", "pascal"),
        ("getHTTPResponseCode", "response"),
        ("getHTTPResponseCode", "http"),
        ("x1y2", "y"),
        ("sha256Hash", "hash"),
        ("sha256Hash", "256"),
        ("IPv4Address", "address"),
        ("a_bC_d", "bc"),
    ],
)
def test_ascii_and_digit_names_split_into_findable_words(tmp_path, monkeypatch, name, query):
    state = _state(tmp_path, monkeypatch, _fields(name, "unrelatedColumn"))
    assert name in _names(state, query, kind="field"), (name, query)


@pytest.mark.parametrize(
    "name", ["aB", "Ab", "A", "a", "ID", "id", "iD", "_", "__x__", "x_", "a1", "1a", "AA11bb"]
)
def test_degenerate_names_index_without_error_and_find_themselves(tmp_path, monkeypatch, name):
    state = _state(tmp_path, monkeypatch, _fields(name, "pad"))
    assert name in _names(state, name.strip("_") or "x", kind="field") or name == "_"


def test_a_name_with_fts_syntax_characters_indexes_and_searches(tmp_path, monkeypatch):
    names = ['quote"Name', "star*Col", "a-b", "x:y", "NEAR", "AND", "OR", "NOT", "col(1)"]
    state = _state(tmp_path, monkeypatch, _fields(*names))
    for query in ["quote", "star", "near", "and", "not", 'a" OR "b', "col*", "x:y", "-b"]:
        search(state, SearchRequest(query=query))  # must not raise an FTS5 syntax error
        search(state, SearchRequest(query=query, match="any"))


UNICODE_CAMEL = [
    ("prixÉlevé", "prix"),
    ("prixÉlevé", "elev"),       # Latin-1 upper-case letter starts the second word
    ("tailleÉcran", "ecran"),
    ("ÉtatCivil", "civil"),
    ("GrößeMaß", "mass"),
    ("ЦенаТовара", "товара"),     # Cyrillic PascalCase
    ("цена_товара", "товара"),
]


@pytest.mark.parametrize(("name", "query"), UNICODE_CAMEL)
def test_unicode_camel_case_names_split_like_ascii_ones(tmp_path, monkeypatch, name, query):
    """The implementation documents that case is read for ASCII only (a ponytail ceiling). The
    spec says camelCase and PascalCase; an accented or Cyrillic capital is a capital."""
    state = _state(tmp_path, monkeypatch, _fields(name, "unrelatedColumn"))
    assert name in _names(state, query, kind="field"), (name, query)


def test_unicode_whole_name_and_cjk_do_not_crash_and_find_themselves(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _fields("顧客名", "Größe", "naïveField", "ΔΤΙΜΗ"))
    assert "顧客名" in _names(state, "顧客名", kind="field")
    assert "Größe" in _names(state, "größe", kind="field")
    assert "naïveField" in _names(state, "naive", kind="field")  # diacritic folding, as before
    assert "naïveField" in _names(state, "field", kind="field")


def test_the_split_does_not_cut_a_word_in_the_middle_of_a_letter_run(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _fields("customerURL", "HTTPServer", "plain"))
    assert _names(state, "ur", kind="field") == ["customerURL"], "prefix of a word is allowed"
    assert _names(state, "rl", kind="field") == []
    assert _names(state, "tpserver", kind="field") == []


# --- D6b: the plural fold ------------------------------------------------------------------------

SINGULAR_PLURAL = [
    ("index", "indexes"), ("status", "statuses"), ("batch", "batches"), ("tax", "taxes"),
    ("box", "boxes"), ("match", "matches"), ("prefix", "prefixes"), ("movie", "movies"),
    ("cookie", "cookies"),
]


@pytest.mark.parametrize(("singular", "plural"), SINGULAR_PLURAL)
def test_the_plural_of_an_es_or_ie_noun_finds_the_singular_name(
    tmp_path, monkeypatch, singular, plural
):
    """D6b: `industries` returns what `industry` returns. The same must hold for the everyday
    schema nouns above (index, status, batch ...), which a plain 'drop the s' fold misses."""
    state = _state(tmp_path, monkeypatch, _fields(f"{singular}_code", "pad_column"))
    assert f"{singular}_code" in _names(state, plural, kind="field"), (singular, plural)


@pytest.mark.parametrize(
    ("asked", "unrelated"),
    [("news", "new_flag"), ("pos", "po_number"), ("gas", "ga_code"),
     ("lens", "len_bytes")],
)
def test_a_word_that_ends_in_s_is_not_merged_with_a_shorter_unrelated_word(
    tmp_path, monkeypatch, asked, unrelated
):
    """`news` is not the plural of `new`; `pos` (point of sale) is not `po` (purchase order)."""
    state = _state(tmp_path, monkeypatch, _fields(unrelated, "news_item", "pad"))
    found = _names(state, asked, kind="field")
    assert unrelated not in found, (asked, found)


def test_exact_name_outranks_its_plural_sibling(tmp_path, monkeypatch):
    """Exact-name queries keep their rank: a table named `invoice` is first for `invoice`, not
    `invoices`, and vice versa, even though the index adds the singular to the plural's text."""
    tables = {
        "invoices": {"fields": [("a_col", "text")]},
        "invoice": {"fields": [("b_col", "text")]},
        "invoiced_total": {"fields": [("c_col", "text")]},
    }
    state = _state(tmp_path, monkeypatch, tables)
    assert _names(state, "invoice", kind="table")[0] == "invoice"
    assert _names(state, "invoices", kind="table")[0] == "invoices"


def test_exact_field_name_outranks_a_plural_field_with_more_matching_words(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _fields("category", "categories", "categories_list"))
    assert _names(state, "category", kind="field")[0] == "category"
    assert _names(state, "categories", kind="field")[0] == "categories"


def test_a_plural_query_does_not_hide_a_whole_word_hit(tmp_path, monkeypatch):
    """Control: plural and singular both present, both found."""
    state = _state(tmp_path, monkeypatch, _fields("industry", "industries"))
    assert set(_names(state, "industries", kind="field")) == {"industry", "industries"}
    assert set(_names(state, "industry", kind="field")) == {"industry", "industries"}


# --- B4: stopwords --------------------------------------------------------------------------------


def _ask(state, question, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(cite_first) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="local-model", model_digest="rev-1", api_key_env="MODEL_KEY"
        )
        return answer_question(state, question, config), list(stub.requests)


def test_a_column_named_number_is_found_by_an_explicit_number_search(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, {"invoice": {"fields": [("number", "integer")]}})
    assert _names(state, "number", kind="field") == ["number"]
    assert _names(state, "invoice number", kind="field", match="any") == ["number"]


def test_a_question_with_a_table_name_and_only_schema_words_still_asks(tmp_path, monkeypatch):
    state = _state(
        tmp_path, monkeypatch,
        {"invoice": {"fields": [("number", "integer")]}, "provenance": {"fields": [("column", "t")]}},
    )
    result, calls = _ask(state, "which column holds the invoice number?", monkeypatch)
    assert "error" not in result, result
    assert len(calls) == 1


def test_a_column_named_table_is_found_with_its_owner_name(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, {"audit": {"fields": [("table", "text")]}})
    hits = search(state, SearchRequest(query="audit table", match="any"))["results"]
    assert any(i["name"] == "table" for i in hits), hits


def test_stopword_check_is_case_insensitive_and_unicode_safe(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, {"part": {"fields": [("flange", "text")]}})
    for question in ["WHICH COLUMN HOLDS THE VESSEL?", "Which Field Stores the vessel", "ÉTÉ ΝΟΥΜΕΡΟ"]:
        result, calls = _ask(state, question, monkeypatch)
        assert result["error"]["code"] == "NO_CATALOGUE_MATCH", question
        assert calls == []


def test_every_listed_schema_word_alone_and_in_plural_is_refused(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, {"part": {"fields": [("flange", "text")]}})
    for word in ["column", "columns", "table", "tables", "field", "fields", "number", "numbers",
                 "holds", "store", "stores", "stored"]:
        result, calls = _ask(state, f"which {word}?", monkeypatch)
        assert result["error"]["code"] == "NO_CATALOGUE_MATCH", word
        assert calls == []


# --- old index -----------------------------------------------------------------------------------


def _old_code_index(tmp_path, snapshot, state):
    """Build the index with the parent commit's code in a git worktree (set up by the caller)."""
    old = Path(os.environ.get("DBIG_OLD_TREE", "/tmp/wt-old"))
    if not (old / "packages/requester/src").is_dir():
        pytest.skip("no old worktree: git worktree add /tmp/wt-old fbaaa7d")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(old / "packages/requester/src")] + [env.get("PYTHONPATH", "")]
    )
    code = (
        "import sys, pathlib;"
        "import dbig_requester.search as s;"
        f"assert str(s.__file__).startswith({str(old)!r}), s.__file__;"
        f"s.index_snapshot(pathlib.Path({str(snapshot)!r}), pathlib.Path({str(state)!r}))"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env, cwd=tmp_path)


def test_an_index_written_by_the_old_code_is_refused_not_misread(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": _fields("accountRevenue", "pad")}}
    )
    state = tmp_path / "old-state"
    _old_code_index(tmp_path, snapshot, state)
    with pytest.raises(ValueError, match="STALE_INDEX"):
        search(state, SearchRequest(query="revenue"))
    index_snapshot(snapshot, state)  # the remedy: reindex in place
    assert _names(state, "revenue", kind="field") == ["accountRevenue"]


def test_ask_on_a_stale_index_reports_a_code_not_a_traceback(tmp_path, monkeypatch):
    """After an upgrade every existing index is stale (INDEX_VERSION). `ask` and the HTTP
    question route must say so as an error code the operator can act on; a bare ValueError out of
    `answer_question` is an HTTP 500 / a traceback."""
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": _fields("accountRevenue", "pad")}}
    )
    state = tmp_path / "old-state"
    _old_code_index(tmp_path, snapshot, state)
    try:
        result, _ = _ask(state, "which column holds the revenue?", monkeypatch)
    except ValueError as error:  # the defect: raw exception escapes
        pytest.fail(f"answer_question raised {error!r} instead of returning an error code")
    assert result["error"]["code"] == "STALE_INDEX"


def test_a_future_index_version_is_also_refused(tmp_path, monkeypatch):
    import sqlite3

    state = _state(tmp_path, monkeypatch, _fields("accountRevenue"))
    database = sqlite3.connect(state / "catalog.sqlite3")
    database.execute("UPDATE meta SET value='999' WHERE key='index_version'")
    database.commit()
    database.close()
    with pytest.raises(ValueError, match="STALE_INDEX"):
        search(state, SearchRequest(query="revenue"))


def test_search_does_not_write_to_the_state_directory(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _fields("accountRevenue"))
    before = {p.name: p.stat().st_mtime_ns for p in state.iterdir()}
    search(state, SearchRequest(query="revenue"))
    assert before == {p.name: p.stat().st_mtime_ns for p in state.iterdir()}
