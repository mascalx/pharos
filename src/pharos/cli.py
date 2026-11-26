"""Simple cross-platform CLI. Explicit config path; no .env file or env overrides."""
from __future__ import annotations

import argparse
import asyncio
import json
from getpass import getpass
from importlib.resources import files
from pathlib import Path
import sys
from uuid import uuid4

import uvicorn

from . import __version__
from .app import create_app
from .config import ConfigError, load_config
from .secrets import SecretError, read_secrets, write_secrets
from .storage import check_data_volume, initialize_volume
from .knowledge_contract import RAGError
from .ai import GatewayError
from .tiiny_core import CoreAI
from .knowledge import RAGEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pharos", description="PHAROS offline-first platform")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new config.toml without overwriting existing files")
    init.add_argument("--directory", default=".")
    for name, help_text in (("serve", "Start the web server"), ("check", "Validate configuration/storage"),
                            ("storage-init", "Initialize a volume marker after checking the mounted path")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", default="config.toml")
        if name == "storage-init":
            command.add_argument("--confirm-path", required=True, help="Exact configured data path to confirm")
    api_key = commands.add_parser("ai-key", help="Configure gateway API key in the protected secrets file")
    api_key_sub = api_key.add_subparsers(dest="ai_key_command", required=True)
    api_key_set = api_key_sub.add_parser("set", help="Prompt securely for the API key (does not echo)")
    api_key_set.add_argument("--config", default="config.toml")
    management_key = commands.add_parser("ai-management-key", help="Configure separate native Tiiny management API key")
    management_key_sub = management_key.add_subparsers(dest="management_key_command", required=True)
    management_key_sub.add_parser("set", help="Prompt securely for Tiiny management API key").add_argument("--config", default="config.toml")
    kb = commands.add_parser("kb", help="Knowledge bases (administrative operations through local CLI)")
    kb_sub = kb.add_subparsers(dest="kb_command", required=True)
    for name in ("list", "create", "documents", "import", "search", "ask"):
        cmd = kb_sub.add_parser(name)
        cmd.add_argument("--config", default="config.toml")
        if name == "create":
            cmd.add_argument("--name", required=True)
            cmd.add_argument("--description", default="")
        if name in ("documents", "import", "search", "ask"):
            cmd.add_argument("--id", required=True, help="Knowledge Base UUID")
        if name == "import":
            cmd.add_argument("--file", required=True)
        if name in ("search", "ask"):
            cmd.add_argument("--query", required=True)
        if name == "ask":
            cmd.add_argument("--model", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            directory = Path(args.directory).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            config_file = directory / "config.toml"
            template = files("pharos").joinpath("config.example.toml").read_text(encoding="utf-8")
            # 'x' avoids clobbering existing configuration even with concurrent installers.
            with config_file.open("x", encoding="utf-8") as stream:
                stream.write(template.replace("REPLACE_ON_INIT", str(uuid4())))
            print(f"Configuration created: {config_file}")
            print("Next: edit config.toml; create/mount data_dir; run 'pharos storage-init --config <file> --confirm-path <data_dir>'")
            return 0
        config = load_config(args.config)
        if args.command == "check":
            status = check_data_volume(config.storage)
            secrets = read_secrets(config.security.secrets_file)
            print(f"PHAROS {__version__} — {config.source}")
            print(f"Data volume: {'OK' if status.available else 'NOT READY'} ({status.reason})")
            print(f"Mounted at data_dir: {'yes' if status.mounted else 'no'}; mount required: {config.storage.require_mount}")
            print(f"AI provider: {config.ai.provider}, {'enabled' if config.ai.enabled else 'disabled'} ({config.ai.base_url}); API key configured: {bool(secrets.get('ai', {}).get('api_key'))}")
            print(f"RAG: {'enabled' if config.rag.enabled else 'disabled'}; embedding model: {config.rag.embedding_model}; Qdrant: {config.rag.qdrant_url} (connection not tested by this command)")
            return 0 if status.available else 1
        if args.command == "storage-init":
            marker = initialize_volume(config.storage, args.confirm_path)
            print(f"Data volume verified and initialized: {marker}")
            return 0
        if args.command == "ai-key":
            key = getpass("Tiiny Gateway / OpenAI-compatible API key: ").strip()
            if not key or "\n" in key or "\r" in key:
                raise SecretError("API key cannot be empty or contain line breaks")
            values = read_secrets(config.security.secrets_file)
            values.setdefault("ai", {})["api_key"] = key
            write_secrets(config.security.secrets_file, values)
            print("API key saved in protected secrets file. Restart PHAROS to activate it.")
            return 0
        if args.command == "ai-management-key":
            key = getpass("Native Tiiny management API key: ").strip()
            if not key or "\n" in key or "\r" in key:
                raise SecretError("API key cannot be empty or contain line breaks")
            values = read_secrets(config.security.secrets_file)
            values.setdefault("ai", {})["management_api_key"] = key
            write_secrets(config.security.secrets_file, values)
            print("Management key saved in protected secrets file. Restart PHAROS to activate it.")
            return 0
        if args.command == "kb":
            secrets = read_secrets(config.security.secrets_file)
            gateway = CoreAI(config.ai, secrets.get("ai", {}).get("api_key", ""),
                             secrets.get("ai", {}).get("management_api_key", ""))
            if not config.rag.enabled:
                raise RAGError('rag_disabled')
            engine = RAGEngine(config, gateway)
            if args.kb_command == "list":
                result = engine.list_kbs()
            elif args.kb_command == "create":
                result = engine.create_kb(args.name, args.description)
            elif args.kb_command == "documents":
                result = engine.documents(args.id)
            elif args.kb_command == "import":
                file = Path(args.file).expanduser().resolve()
                if file.stat().st_size > config.rag.max_document_mb * 1024 * 1024:
                    raise RAGError("document_empty_or_too_large")
                result = asyncio.run(engine.ingest(args.id, file.name, file.read_bytes()))
            elif args.kb_command == "search":
                result = asyncio.run(engine.search([args.id], args.query))
            elif args.kb_command == "ask":
                result = asyncio.run(engine.answer([args.id], args.query, args.model))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "serve":
            # If NFS is absent, serve the diagnostic UI but never create data directories.
            app = create_app(config)
            uvicorn.run(app, host=config.server.host, port=config.server.port, proxy_headers=False)
            return 0
    except (ConfigError, SecretError, RAGError, GatewayError, ValueError, OSError) as exc:
        print(f"PHAROS ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
