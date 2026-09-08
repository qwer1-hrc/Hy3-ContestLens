from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hy3_contestlens.service import ServiceHub


hub = ServiceHub()
mcp = FastMCP("Hy3-ContestLens Problem Resources")


@mcp.tool()
def inspect_path_scope(resource_scope_id: str) -> dict:
    """Inspect public metadata for an already authorized read-only scope."""
    return hub.resources.inspect_scope(resource_scope_id)


@mcp.tool()
def validate_scoped_path(resource_scope_id: str, relative_path: str, expected_kind: str | None = None, expected_extensions: list[str] | None = None) -> dict:
    """Validate a relative path without exposing its host absolute path."""
    return hub.resources.validate_scoped_path(resource_scope_id, relative_path, expected_kind, expected_extensions)


@mcp.tool()
def list_scoped_directory(resource_scope_id: str, relative_path: str = ".", recursive: bool = False, max_depth: int | None = None, max_entries: int | None = None) -> dict:
    """List allowed entries in an authorized scope; links and unrelated extensions are omitted."""
    return hub.resources.list_scoped_directory(resource_scope_id, relative_path, recursive, max_depth, max_entries)


@mcp.tool()
def find_problem_assets(resource_scope_id: str, dataset_id: str, problem_id: str, title_zh: str, io_basename: str) -> dict:
    """Find statement and paired test candidates without returning answer contents."""
    if dataset_id != hub.catalog.get(problem_id).dataset_id:
        raise ValueError("DATASET_NOT_FOUND")
    return hub.resources.find_problem_assets(resource_scope_id, problem_id, title_zh, io_basename)


@mcp.tool()
def read_problem_document(resource_scope_id: str, problem_id: str, cursor: int = 0, max_chars: int = 12000) -> dict:
    """Read the frozen statement binding as untrusted problem content."""
    binding = hub.store.get_binding(problem_id)
    if binding["scope_id"] != resource_scope_id:
        raise ValueError("RESOURCE_BINDING_MISMATCH")
    return hub.resources.read_problem_document(resource_scope_id, binding["document"], cursor, max_chars)


@mcp.tool()
def inspect_test_dataset(resource_scope_id: str, problem_id: str) -> dict:
    """Inspect .in/.out/.ans pairing and hashes; never returns standard-answer content."""
    return hub.resources.inspect_test_dataset(resource_scope_id, problem_id)


@mcp.tool()
def read_problem_sample_input(resource_scope_id: str, relative_path: str) -> dict:
    """Read only manifest-designated public samples. NOIP private inputs are denied by default."""
    raise ValueError("ACCESS_DENIED: no public sample inputs are configured")


if __name__ == "__main__":
    mcp.run(transport="stdio")
