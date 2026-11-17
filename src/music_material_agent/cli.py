from __future__ import annotations

import argparse
import json
from typing import Any

import uvicorn

from .doctor import run_doctor
from .service import MusicMaterialService


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="music-material")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Check runtime dependencies and project paths.")
    commands.add_parser("mcp", help="Run the stdio MCP server.")

    mcp_http = commands.add_parser(
        "mcp-http", help="Run one shared localhost MCP endpoint for all agent windows."
    )
    mcp_http.add_argument("--host", default="127.0.0.1")
    mcp_http.add_argument("--port", type=int, default=8766)

    serve = commands.add_parser("serve", help="Run the shared local HTTP status service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    inspect = commands.add_parser("inspect", help="Inspect an XLSX workbook.")
    inspect.add_argument("path")
    inspect.add_argument("--sample-rows", type=int, default=5)

    create = commands.add_parser("create-job", help="Create a durable workbook job.")
    create.add_argument("workbook_path")
    create.add_argument("--sheet-name")
    create.add_argument("--header-row", type=int, default=1)
    create.add_argument("--created-by", default="cli")

    status = commands.add_parser("status", help="Show one job's state.")
    status.add_argument("job_id")

    commands.add_parser("list-jobs", help="List durable jobs.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "doctor":
        _print(run_doctor())
        return
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return
    if args.command == "mcp-http":
        from .mcp_server import run_http

        run_http(args.host, args.port)
        return
    if args.command == "serve":
        uvicorn.run("music_material_agent.api:app", host=args.host, port=args.port, reload=False)
        return
    service = MusicMaterialService()
    if args.command == "inspect":
        _print(service.inspect_workbook(args.path, sample_rows=args.sample_rows))
    elif args.command == "create-job":
        _print(
            service.create_job(
                args.workbook_path,
                sheet_name=args.sheet_name,
                header_row=args.header_row,
                created_by=args.created_by,
            )
        )
    elif args.command == "status":
        _print(service.job_status(args.job_id))
    elif args.command == "list-jobs":
        _print(service.list_jobs())


if __name__ == "__main__":
    main()
