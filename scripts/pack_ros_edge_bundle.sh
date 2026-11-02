#!/usr/bin/env bash
# Build a minimal ADP tarball for robot-edge ROS validation (scenarios A/B/C + offline demos).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${ROOT}/dist"
NAME="adp-ros-edge-${STAMP}"
STAGE="${OUT_DIR}/${NAME}"

rm -rf "${STAGE}"
mkdir -p "${STAGE}"

copy_tree() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "${dst}")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' "${src}/" "${dst}/"
  else
    mkdir -p "${dst}"
    cp -a "${src}/." "${dst}/"
    find "${dst}" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  fi
}

# Core platform
copy_tree "${ROOT}/adp" "${STAGE}/adp"

# Examples needed on the robot
copy_tree "${ROOT}/examples/ros_robot_live_validation" "${STAGE}/examples/ros_robot_live_validation"
copy_tree "${ROOT}/examples/aquaculture_feeding_loop" "${STAGE}/examples/aquaculture_feeding_loop"
# Make examples a package for `python -m examples....`
touch "${STAGE}/examples/__init__.py"

# Focused tests (optional smoke on edge)
mkdir -p "${STAGE}/tests"
cp -a "${ROOT}/tests/test_examples_ros_robot_live_validation.py" "${STAGE}/tests/"
cp -a "${ROOT}/tests/test_examples_aquaculture_feeding_loop.py" "${STAGE}/tests/" 2>/dev/null || true
touch "${STAGE}/tests/__init__.py"

# Project metadata
cp -a "${ROOT}/pyproject.toml" "${STAGE}/"
cp -a "${ROOT}/.gitignore" "${STAGE}/" 2>/dev/null || true

# Edge-specific docs / env template (overwrite root README with edge guide)
cp -a "${ROOT}/deploy/ros_edge/EDGE_README.md" "${STAGE}/README.md"
cp -a "${ROOT}/deploy/ros_edge/env.example" "${STAGE}/.env.example"

# Exclude aquaculture live validation / MySQL / heavy reports by design

mkdir -p "${OUT_DIR}"
TAR="${OUT_DIR}/${NAME}.tar.gz"
tar -C "${OUT_DIR}" -czf "${TAR}" "${NAME}"

# Also write a stable latest symlink/copy name
cp -f "${TAR}" "${OUT_DIR}/adp-ros-edge-latest.tar.gz"

echo "Built: ${TAR}"
echo "Also:  ${OUT_DIR}/adp-ros-edge-latest.tar.gz"
du -h "${TAR}"
echo "--- contents (top) ---"
tar -tzf "${TAR}" | head -n 40
echo "... ($(tar -tzf "${TAR}" | wc -l) entries)"
