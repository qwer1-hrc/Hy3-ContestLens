from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hy3_contestlens.service import ServiceHub


hub = ServiceHub()
mcp = FastMCP("Hy3-ContestLens Judge")


@mcp.tool()
def compile_cpp(problem_id: str, source_artifact_id: str, source_sha256: str, compile_profile: str = "noip2018_cpp") -> dict:
    manifest = hub.catalog.get(problem_id)
    result = hub.judge.compile_cpp(problem_id, source_artifact_id, source_sha256, compile_profile)
    return result.model_dump(mode="json")


@mcp.tool()
def check_answer(compile_artifact_id: str, dataset_id: str, problem_id: str, test_ids: list[str] | None = None) -> dict:
    return hub.judge.check_answer(compile_artifact_id, dataset_id, problem_id, test_ids).model_dump(mode="json")


@mcp.tool()
def run_single_test(compile_artifact_id: str, dataset_id: str, problem_id: str, test_id: str) -> dict:
    return hub.judge.check_answer(compile_artifact_id, dataset_id, problem_id, [test_id]).model_dump(mode="json")


@mcp.tool()
def inspect_problem_manifest(problem_id: str) -> dict:
    manifest = hub.catalog.get(problem_id)
    memory, source = hub.catalog.effective_memory(problem_id)
    return {**manifest.model_dump(mode="json"), "effective_memory_limit_mb": memory, "memory_limit_source": source}


@mcp.tool()
def sandbox_healthcheck() -> dict:
    return hub.judge.healthcheck()


@mcp.tool()
def fuzz_small_case(problem_id: str, seed: int = 0, count: int = 10) -> dict:
    return {"status": "NOT_IMPLEMENTED_FOR_REFERENCE_SOLVERS", "problem_id": problem_id, "seed": seed, "count": count, "message": "Formal hidden answers are never exposed; add a trusted small-case generator before enabling this tool."}


if __name__ == "__main__":
    mcp.run(transport="stdio")
