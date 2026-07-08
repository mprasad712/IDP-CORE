"""Documents live in `<agent.id>(<name>_<env>_<version>)/`, and old documents keep resolving.

The directory used to be `idp_agents.id` — an id that appears nowhere in the API, the URL, or the UI.
`POST /api/run/582bc691-…/document` wrote into `8fcd9733-…/`.

The whole change is only safe because `IdpDocument.file_path` is the ONLY record of where a document was
written, and every reader splits it. Anything that reconstructs the directory from `doc.agent_id` silently
sends new documents to the old folder (or old documents to the new one) — hence the structural guards.
"""

from uuid import uuid4

import pytest

from agentcore.services.idp.storage_scope import scope_of, slugify_agent_name, storage_scope


def test_scope_is_the_base_agent_id_with_a_readable_suffix():
    agent_id = "582bc691-298b-423e-8daf-55a7f56a96c7"
    assert storage_scope(agent_id, "testing universal agent", "uat", "v5") == (
        f"{agent_id}(testing-universal-agent_uat_v5)"
    )
    # The id in the URL is the id on disk — that's the point.
    assert scope_of(f"{storage_scope(agent_id, 'x', 'uat', 'v5')}/idp_1.pdf").startswith(agent_id)


def test_env_and_version_describe_the_documents_inside():
    a = "abc"
    assert storage_scope(a, "n", "uat", "v5").endswith("(n_uat_v5)")
    assert storage_scope(a, "n", 2, "v1").endswith("(n_prod_v1)")       # numeric env encoding
    assert storage_scope(a, "n", "prod", None).endswith("(n_prod_latest)")  # active version, not a lie
    assert storage_scope(a, "n").endswith("(n_dev_draft)")               # the draft canvas
    assert storage_scope(a, "n", "dev", "v9").endswith("(n_dev_draft)")  # a version is meaningless for dev


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("testing universal agent", "testing-universal-agent"),
        ("My/Agent: *v2? <prod>", "My-Agent-v2-prod"),   # every char Windows rejects
        ("a\\b|c\"d", "a-b-c-d"),
        ("...trim...", "trim"),
        ("   ", "agent"),                                  # never empty
        (None, "agent"),
        ("x" * 200, "x" * 40),                             # bounded
    ],
)
def test_agent_names_are_reduced_to_a_path_safe_slug(raw, expected):
    got = slugify_agent_name(raw)
    assert got == expected
    assert not (set(got) & set('\\/:*?"<>|')), "slug contains a character Windows rejects"
    assert got and not got.startswith((".", "-")) and not got.endswith((".", "-"))


def test_scope_of_reads_the_stored_path_verbatim():
    """Old and new layouts both resolve, because nothing is reconstructed."""
    assert scope_of("8fcd9733-eaf9-40d1-ac1f-07ed70ec6cf6/idp_x.pdf") == "8fcd9733-eaf9-40d1-ac1f-07ed70ec6cf6"
    assert scope_of("582bc691(agent_uat_v5)/idp_x.pdf") == "582bc691(agent_uat_v5)"
    assert scope_of("582bc691(agent_uat_v5)/corrected/corrected_idp_x.pdf") == "582bc691(agent_uat_v5)"
    assert scope_of("bare.pdf") == ""
    assert scope_of(None) == ""


def test_a_renamed_agent_cannot_orphan_existing_documents():
    """A rename changes only where NEW documents go. Existing file_paths still point at their own folder."""
    agent_id = str(uuid4())
    before = storage_scope(agent_id, "Old Name", "uat", "v5")
    after = storage_scope(agent_id, "New Name", "uat", "v5")
    assert before != after
    existing = f"{before}/idp_doc.pdf"
    assert scope_of(existing) == before, "a rename must not change where an existing document is read from"


# ───────────────────────── structural guards ─────────────────────────
def test_nothing_rebuilds_the_storage_directory_from_doc_agent_id():
    """`agent_scope = str(doc.agent_id)` sends new documents to the OLD directory. It must not come back.

    Repo-wide, not per-function: this pattern lived in THREE places (pipeline._run, graph_native.process,
    and components/IDP/document_upload — the native engine's entry node). Fixing only the first two left
    the pipeline writing to the new folder and reading from the old one:

        FileNotFoundError: File idp_9ec8e74c-….pdf not found in agent 8fcd9733-…
    """
    import pathlib
    import re

    from agentcore.services.idp import storage_scope as _mod

    root = pathlib.Path(_mod.__file__).resolve().parents[1]        # .../agentcore/services
    trees = [root / "idp", root.parents[0] / "components" / "IDP", root / "trigger"]

    # `doc.agent_id` is the IdpAgent row id; using it as a storage scope is the bug.
    bad = re.compile(r"(agent_scope|agent_id)\s*=\s*str\(\s*doc\.agent_id\s*\)")
    offenders = []
    for tree in trees:
        for path in tree.rglob("*.py"):
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                code = line.split("#", 1)[0]
                if bad.search(code) and "idp_agent_id" not in code:
                    offenders.append(f"{path.name}:{i}: {line.strip()}")

    assert not offenders, (
        "these rebuild the storage directory instead of reading IdpDocument.file_path:\n  "
        + "\n  ".join(offenders)
    )


def test_the_pipeline_and_the_native_entry_node_both_read_file_path():
    import inspect

    from agentcore.components.IDP import document_upload
    from agentcore.services.idp import pipeline

    assert "_split_storage_path(doc.file_path)" in inspect.getsource(pipeline._run)
    assert "scope_of(file_path)" in inspect.getsource(document_upload)


def test_flow_logs_land_beside_their_document():
    """Both engines write flow_logs/ into the document's own directory."""
    import inspect

    from agentcore.services.idp import pipeline
    from agentcore.services.idp.graph_native import process

    fixed = inspect.getsource(pipeline._fail_and_log) if hasattr(pipeline, "_fail_and_log") else inspect.getsource(pipeline)
    assert "agent_id=str(doc.agent_id)" not in fixed, "the fixed pipeline still reconstructs the flow-log dir"
    assert "scope_of(doc.file_path)" in inspect.getsource(process), "the native engine reconstructs the flow-log dir"


def test_every_path_minting_site_uses_the_helper():
    """documents.upload / run_api / connectors must all agree on the directory name."""
    import inspect

    from agentcore.api.idp import documents, run_api
    from agentcore.services.trigger import service as trigger

    for mod in (documents, run_api, trigger):
        src = inspect.getsource(mod)
        assert "storage_scope(" in src, f"{mod.__name__} does not use the shared scope helper"

    assert 'file_path=f"{idp_agent.id}/' not in inspect.getsource(documents)
    assert 'file_path=f"{idp_agent.id}/' not in inspect.getsource(run_api)
