#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python}"
HOST="${XMU_ROLLCALL_WEB_HOST:-127.0.0.1}"
PORT="${XMU_ROLLCALL_WEB_PORT:-8765}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "未找到 Python 虚拟环境：${PYTHON_BIN}" >&2
    echo "请先执行：python3 -m venv .venv && .venv/bin/pip install -r xmu-rollcall-cli/requirements-web.txt" >&2
    exit 1
fi

if [[ "${HOST}" != "127.0.0.1" && "${HOST}" != "localhost" && "${HOST}" != "::1" && -z "${XMU_ROLLCALL_WEB_PASSWORD:-}" ]]; then
    echo "监听非本机地址前必须设置 XMU_ROLLCALL_WEB_PASSWORD。" >&2
    exit 1
fi

export PYTHONPATH="${REPO_ROOT}/xmu-rollcall-cli${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" -m xmu_rollcall.web --host "${HOST}" --port "${PORT}" "$@"
