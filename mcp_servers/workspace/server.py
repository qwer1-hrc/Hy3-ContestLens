from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hy3_contestlens.service import ServiceHub


hub = ServiceHub()
mcp = FastMCP("Hy3-ContestLens Code Workspace")


@mcp.tool()
def create_cpp_submission(run_id: str, problem_id: str, source_code: str, created_by: str = "solver") -> dict:
    return hub.workspace.create_cpp_submission(run_id, problem_id, source_code, created_by)


@mcp.tool()
def read_cpp_submission(run_id: str, submission_id: str, revision_id: str) -> dict:
    return hub.workspace.read_cpp_submission(run_id, submission_id, revision_id)


@mcp.tool()
def replace_cpp_submission(run_id: str, submission_id: str, base_revision_id: str, base_sha256: str, source_code: str, repair_round: int, repair_plan_id: str, reason: str) -> dict:
    return hub.workspace.replace_cpp_submission(run_id, submission_id, base_revision_id, base_sha256, source_code, repair_round, repair_plan_id, reason)


@mcp.tool()
def apply_cpp_patch(run_id: str, submission_id: str, base_revision_id: str, base_sha256: str, patch: str, repair_round: int, repair_plan_id: str, reason: str) -> dict:
    return hub.workspace.apply_cpp_patch(run_id, submission_id, base_revision_id, base_sha256, patch, repair_round, repair_plan_id, reason)


@mcp.tool()
def list_cpp_revisions(run_id: str, submission_id: str) -> dict:
    return hub.workspace.list_cpp_revisions(run_id, submission_id)


@mcp.tool()
def get_cpp_metadata(run_id: str, submission_id: str, revision_id: str) -> dict:
    return hub.workspace.get_cpp_metadata(run_id, submission_id, revision_id)


@mcp.tool()
def freeze_cpp_revision(run_id: str, submission_id: str, revision_id: str, expected_sha256: str) -> dict:
    return hub.workspace.freeze_cpp_revision(run_id, submission_id, revision_id, expected_sha256)


if __name__ == "__main__":
    mcp.run(transport="stdio")

