import json

from app.release_review import (
    _artifact_diff,
    _dependency_inventory,
    _risk_summary,
    _sbom,
    _source_tree_sha,
)


def _content(path:str,value:str)->dict:
    return {
        "relative_path":path,
        "content_bytes":value.encode("utf-8"),
    }


def test_dependency_inventory_and_sbom():
    contents=[
        _content(
            "requirements.txt",
            "httpx==0.28.1\nfastapi>=0.118\n",
        ),
        _content(
            "pyproject.toml",
            """
[project]
name = "demo"
dependencies = ["pydantic>=2.10"]

[project.optional-dependencies]
test = ["pytest==8.4.2"]
""",
        ),
        _content(
            "web/package.json",
            json.dumps({
                "dependencies":{"react":"^19.0.0"},
                "devDependencies":{"vite":"^7.0.0"},
            }),
        ),
    ]
    deps=_dependency_inventory(contents)
    names={(x["ecosystem"],x["name"]) for x in deps}
    assert ("pypi","httpx") in names
    assert ("pypi","fastapi") in names
    assert ("pypi","pydantic") in names
    assert ("pypi","pytest") in names
    assert ("npm","react") in names
    assert ("npm","vite") in names

    sbom=_sbom(deps)
    assert sbom["bomFormat"]=="CycloneDX"
    assert sbom["specVersion"]=="1.5"
    assert len(sbom["components"])==len(deps)


def test_artifact_diff_and_tree_hash_are_deterministic():
    before=[
        {"relative_path":"a.py","sha256":"1"*64,"byte_size":10,"media_type":"text/x-python"},
        {"relative_path":"removed.txt","sha256":"2"*64,"byte_size":2,"media_type":"text/plain"},
    ]
    after=[
        {"relative_path":"a.py","sha256":"3"*64,"byte_size":12,"media_type":"text/x-python"},
        {"relative_path":"new.txt","sha256":"4"*64,"byte_size":4,"media_type":"text/plain"},
    ]
    diff=_artifact_diff(after,before)
    status={x["relative_path"]:x["status"] for x in diff}
    assert status=={
        "a.py":"MODIFIED",
        "new.txt":"ADDED",
        "removed.txt":"REMOVED",
    }
    assert _source_tree_sha(after)==_source_tree_sha(list(reversed(after)))
    assert len(_source_tree_sha(after))==64


def test_risk_summary_flags_static_review_only():
    contents=[
        _content(
            "unsafe.py",
            "import subprocess\nsubprocess.run(['echo','x'])\n",
        ),
        _content(
            "client.py",
            "import httpx\nhttpx.get('https://example.com')\n",
        ),
    ]
    risk=_risk_summary(contents,[])
    assert risk["risk_level"]=="HIGH"
    rules={x["rule"] for x in risk["findings"]}
    assert "shell_execution" in rules
    assert "network_client" in rules
    assert "does not approve or deploy" in risk["note"]
