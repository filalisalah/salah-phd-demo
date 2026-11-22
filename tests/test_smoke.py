"""
Smoke tests: the module imports cleanly and exposes the expected public API.

Importing ``app`` must NOT start a Streamlit server, call ``main()``, perform
any network I/O, or start the local static file server. ``main()`` is guarded
by ``if __name__ == "__main__"`` and the static server is only started lazily
inside ``_ensure_static_server()`` (decorated with ``st.cache_resource``), so a
plain import is side-effect free.
"""

from __future__ import annotations

import importlib


def test_import_app_succeeds():
    module = importlib.import_module("app")
    assert module is not None


def test_key_constants_exist():
    import app

    assert app.AGENT_GRID == "Grid_Dispatch_Agent"
    assert app.AGENT_REGULATORY == "Regulatory_Compliance_Agent"
    assert app.AGENT_FINANCIAL == "Financial_Risk_Agent"
    assert app.AGENT_ORDER == [
        app.AGENT_GRID,
        app.AGENT_REGULATORY,
        app.AGENT_FINANCIAL,
    ]
    assert app.VALID_STATUSES == {"APPROVE", "REJECT", "TIMEOUT"}
    assert len(app.SCENARIOS) == 3


def test_key_functions_are_callable():
    import app

    assert callable(app.arbitrate)
    assert callable(app.build_verdict)
    assert callable(app.normalize_opinion)
    assert callable(app.timeout_opinion)
    assert callable(app.get_offline_opinions)
    assert callable(app.main)


def test_verdict_meta_covers_all_verdicts():
    import app

    assert set(app.VERDICT_META.keys()) == app.VERDICTS


def test_header_rows_structure():
    import app

    assert len(app.HEADER_ROWS) == 2
    assert [len(row) for row in app.HEADER_ROWS] == [4, 4]
    assert [entry[0] for entry in app.HEADER_ROWS[0]] == [
        "diploma",
        "resume",
        "linkedin",
        "elearning",
    ]
    assert [entry[0] for entry in app.HEADER_ROWS[1]] == [
        "cover_letter",
        "research_proposal",
        "architectural_blueprint",
        "technical_statement",
    ]


def test_cover_letter_label_and_no_old_combined_label():
    import app

    assert app.DOCUMENT_TITLES["cover_letter"] == "✉️ Cover letter"
    old_label = "Cover letter & PHD Proposal"
    assert old_label not in app.DOCUMENT_TITLES.values()
    assert all(
        old_label not in [entry[1] for entry in row] for row in app.HEADER_ROWS
    )


def test_document_files_exist():
    import app

    for filename in app.DOCUMENT_FILES.values():
        assert (app.RESOURCES_DIR / filename).is_file(), filename


def test_import_does_not_start_static_server():
    # The local HTTP server is only started lazily on first document view.
    # A plain import must leave the module-level state untouched.
    import app

    assert hasattr(app, "_ensure_static_server")
    # Calling the cached function is not done at import time; the module must
    # not have bound a server port at module scope.
    assert not hasattr(app, "_STATIC_SERVER_PORT")
