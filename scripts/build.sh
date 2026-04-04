#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PYTHON_BIN="${PYTHON_BIN:-python3.12}"
BUILD_VENV_DIR="${BUILD_VENV_DIR:-${ROOT_DIR}/.build/build-venv}"

if ! command -v "${DEFAULT_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python 3.12 is required for the desktop build." >&2
  echo "Set PYTHON_BIN to a valid Python 3.12 executable if needed." >&2
  exit 1
fi

echo "Using Python: $(${DEFAULT_PYTHON_BIN} --version)"
echo "Creating build virtual environment in ${BUILD_VENV_DIR}"
"${DEFAULT_PYTHON_BIN}" -m venv "${BUILD_VENV_DIR}"

VENV_PYTHON_BIN="${BUILD_VENV_DIR}/bin/python"

echo "Installing project and build dependencies into the build virtual environment"
"${VENV_PYTHON_BIN}" -m pip install --upgrade pip
"${VENV_PYTHON_BIN}" -m pip install -e "${ROOT_DIR}[build]"

echo "Cleaning previous build outputs"
rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/dist"

echo "Building desktop bundle with PyInstaller"
"${VENV_PYTHON_BIN}" -m PyInstaller --noconfirm --clean "${ROOT_DIR}/web-assets-extractor.spec"

echo "Build completed"
echo "Bundle available in ${ROOT_DIR}/dist/web-assets-extractor"
echo "App bundle available in ${ROOT_DIR}/dist/web-assets-extractor.app"
