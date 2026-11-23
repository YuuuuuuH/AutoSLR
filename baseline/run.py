import argparse
import importlib
import sys

COMMANDS = {
    "download-dataset": ("download_dataset", "Download pinned article metadata and human labels"),
    "download-pdfs": ("collect_pdfs", "Download and validate open-access PDFs for OCR"),
    "screen": ("screen", "Run title and abstract screening with offline preflight"),
    "batch": ("batch", "Prepare, submit, inspect and collect Batch screening jobs"),
}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        return importlib.import_module(f"scripts.{COMMANDS[sys.argv[1]][0]}").main(sys.argv[2:])
    parser = argparse.ArgumentParser(description='Run the organized AgentSLR screening tools from this directory.')
    parser.add_argument("command", choices=COMMANDS, help=" | ".join(f"{k} {v[1]}" for k, v in COMMANDS.items()))
    parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
