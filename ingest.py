#!/usr/bin/env python3
"""asdlc-ingest — Index documents into the RAG knowledge base.

Subcommands:
    load       Load documents from files/directories into a collection
    list       List indexed collections and document counts
    clear      Clear all documents from a collection

Usage:
    asdlc-ingest load --path DOCS_PATH --collection COLLECTION [--repo REPO]
    asdlc-ingest list
    asdlc-ingest clear --collection COLLECTION
"""

import argparse
import asyncio
import glob
import sys
from pathlib import Path

from config import get_settings
from rag import RAGStore


async def cmd_load(args):
    """Load documents from files into a collection."""
    path = Path(args.path)

    if not path.exists():
        print(f"Error: Path does not exist: {path}", file=sys.stderr)
        return 1

    settings = get_settings()
    if not settings.rag_enabled:
        print("Error: RAG is not enabled (RAG_ENABLED=false)", file=sys.stderr)
        return 1

    # Auto-discover .md and .txt files
    if path.is_dir():
        files = list(glob.glob(str(path / "**/*.md"), recursive=True))
        files.extend(glob.glob(str(path / "**/*.txt"), recursive=True))
        if not files:
            print(f"Warning: No .md or .txt files found in {path}", file=sys.stderr)
            return 1
        print(f"Found {len(files)} documents to index")
    else:
        files = [str(path)]

    # Initialize RAG store
    rag_store = RAGStore(settings.rag_db_path, settings.rag_embedding_model)
    await rag_store.setup()

    try:
        total_indexed = 0
        for file_path in files:
            print(f"Indexing {file_path}...")
            await rag_store.index_file(
                args.collection,
                file_path,
                chunk_size=settings.rag_chunk_size,
                repo=args.repo or "global",
            )
            total_indexed += 1

        print(f"✓ Successfully indexed {total_indexed} file(s) into {args.collection}")
        return 0
    except Exception as e:
        print(f"Error: Failed to index documents: {e}", file=sys.stderr)
        return 1
    finally:
        await rag_store.cleanup()


async def cmd_list(args):
    """List indexed collections and document counts."""
    settings = get_settings()
    if not settings.rag_enabled:
        print("Error: RAG is not enabled", file=sys.stderr)
        return 1

    rag_store = RAGStore(settings.rag_db_path, settings.rag_embedding_model)
    await rag_store.setup()

    try:
        stats = await rag_store.list_collections()
        print("\n=== RAG Collections ===\n")
        total = 0
        for collection, count in sorted(stats.items()):
            print(f"  {collection:30} {count:6d} documents")
            total += count
        print(f"\n  {'Total':30} {total:6d} documents\n")
        return 0
    except Exception as e:
        print(f"Error: Failed to list collections: {e}", file=sys.stderr)
        return 1
    finally:
        await rag_store.cleanup()


async def cmd_clear(args):
    """Clear all documents from a collection."""
    settings = get_settings()
    if not settings.rag_enabled:
        print("Error: RAG is not enabled", file=sys.stderr)
        return 1

    if args.collection not in RAGStore.COLLECTIONS:
        print(f"Error: Unknown collection '{args.collection}'", file=sys.stderr)
        print(f"Valid collections: {', '.join(RAGStore.COLLECTIONS.keys())}", file=sys.stderr)
        return 1

    rag_store = RAGStore(settings.rag_db_path, settings.rag_embedding_model)
    await rag_store.setup()

    try:
        await rag_store.clear_collection(args.collection)
        print(f"✓ Cleared collection '{args.collection}'")
        return 0
    except Exception as e:
        print(f"Error: Failed to clear collection: {e}", file=sys.stderr)
        return 1
    finally:
        await rag_store.cleanup()


async def main():
    parser = argparse.ArgumentParser(
        description="Index documents into RAG knowledge base", prog="asdlc-ingest"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # load subcommand
    load_parser = subparsers.add_parser("load", help="Load documents from files/directories")
    load_parser.add_argument(
        "--path", required=True, help="Path to file or directory containing documents"
    )
    load_parser.add_argument(
        "--collection",
        required=True,
        choices=list(RAGStore.COLLECTIONS.keys()),
        help="Target collection",
    )
    load_parser.add_argument(
        "--repo",
        help="Repository scope for metadata (default: global)",
    )

    # list subcommand
    subparsers.add_parser("list", help="List indexed collections")

    # clear subcommand
    clear_parser = subparsers.add_parser("clear", help="Clear a collection")
    clear_parser.add_argument(
        "--collection",
        required=True,
        choices=list(RAGStore.COLLECTIONS.keys()),
        help="Collection to clear",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "load":
        return await cmd_load(args)
    elif args.command == "list":
        return await cmd_list(args)
    elif args.command == "clear":
        return await cmd_clear(args)

    return 0


def ingest_main():
    """Entry point for asdlc-ingest CLI."""
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


if __name__ == "__main__":
    ingest_main()
