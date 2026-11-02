# Example: ROS Robot Live Validation (Scenarios A + B + C)

Validates that ADP's Sense → Understand → Decide → Act → Observe → Learn loop
can drive **task-level** decisions for the `ros_ws` mobile robot, without
owning the real-time `/cmd_vel` or Nav2 local planner loops.

| Scenario | What it proves |
|---|---|
| **A** | Utterance → resolve waypoint → LLM rationale → approval → Act (goto) |
| **B** | G5-style intent hub (chat/search/goto/drive/arm/stop/exit/unsupported) |
| **C** | VLM object search: offline fake / file VLM / live detect_once or object_search |

This directory does **not** import `ros_ws` Python packages. Place aliases and
intent heuristics are ported into `domain.py` from
`ros_ws/src/test/voice_hub_test.py`. VLM prompt/parse mirrors
`vlm_search_common.py` in `vlm_detect.py`.

## Safety model

1. **Default Act is dry-run** — records the would-be command; does not start ROS.
2. **`--live` enables**:
   - A: `goto` → `go_waypoint_test.py`
   - C `motion=once`: `vlm_detect_once` → `vlm_detect_once_test.py`
   - C `motion=wander|nav`: `vlm_object_search` → `vlm_object_search_test.py`
   Interpreter: `ADP_ROS_PYTHON` → `ROS_PYTHON` → `/usr/bin/python3`.
   Operator must pre-launch the needed stacks; this demo never auto-launches them.
3. Real Act for B's `drive` / `arm_routine` / `search` remains **skipped** even
   with `--live` (use scenario **C** for VLM search).
4. Physical / search decisions set `requires_approval=True`. Non-physical B
   intents (chat/stop/exit/…) may complete without an approval pause.
5. Soft C outcomes `announce_found` / `report_miss` (offline/file VLM already
   ran in Understand) stay log-only even under `--live`.

## Setup

```bash
cd /home/gmm/srv/ADP
pip install -e ".[dev]"
# optional for loading real waypoints.yaml:
pip install pyyaml

# optional env (see ADP .env)
# ADP_ROS_WS=/home/gmm/srv/ros_ws
# ADP_ROS_WAYPOINTS=/home/gmm/srv/ros_ws/src/car_nav2/map/waypoints.yaml
# ADP_LLM_* for --real-llm / file VLM
# ADP_VLM_MODEL or VLM_MODEL for multimodal model id (scenario C --image)
```

## Run (offline dry-run)

```bash
# A: goto
python -m examples.ros_robot_live_validation.run_demo \
  --scenario a --utterance "去桌子"

# B: intent hub
python -m examples.ros_robot_live_validation.run_demo \
  --scenario b --utterance "帮我找绿色瓶子" --auto-approve

python -m examples.ros_robot_live_validation.run_demo \
  --scenario b --utterance "介绍一下你自己" --auto-approve

# C: fake VLM (no camera / no image) → announce_found or report_miss
python -m examples.ros_robot_live_validation.run_demo \
  --scenario c --utterance "帮我找红色水杯" --auto-approve

# C: miss heuristic target
python -m examples.ros_robot_live_validation.run_demo \
  --scenario c --target "火星独角兽" --auto-approve

# C: still-image VLM (needs ADP_LLM_* + vision-capable model)
python -m examples.ros_robot_live_validation.run_demo \
  --scenario c --target "红色水杯" --image /path/to/frame.jpg --auto-approve
```

## Run (edge, live)

车端最小迁移包：开发机执行 `bash scripts/pack_ros_edge_bundle.sh`，产物
`dist/adp-ros-edge-latest.tar.gz`，说明见包内 `README.md`
（源：`deploy/ros_edge/EDGE_README.md`）。

```bash
# A — Terminals on the robot (operator):
#   ros2 launch car_base car_base.launch.py
#   ros2 launch car_nav2 car_nav2.launch.py slam:=False

export ADP_ROS_PYTHON=/usr/bin/python3   # python that can import rclpy
python -m examples.ros_robot_live_validation.run_demo \
  --scenario a --utterance "去桌子" --live --ros-ws /home/gmm/srv/ros_ws

# C P0 — car still + camera (same stacks as VLM detect_once test; no wander)
python -m examples.ros_robot_live_validation.run_demo \
  --scenario c --target "红色水杯" --live --motion once --ros-ws /home/gmm/srv/ros_ws

# C P1 — needs motion stack (wander or nav) as required by vlm_object_search_test
python -m examples.ros_robot_live_validation.run_demo \
  --scenario c --target "绿色瓶子" --live --motion wander --ros-ws /home/gmm/srv/ros_ws
```

## Files

| File | Role |
|---|---|
| `domain.py` | Waypoints, aliases, heuristic intent (no rclpy) |
| `vlm_detect.py` | VLM prompt/parse, fake detect, file multimodal HTTP |
| `ros_bridge.py` | dry-run / goto / vlm_detect_once / vlm_object_search |
| `stages.py` | Six-stage implementations for A/B/C |
| `loop.py` | LoopSpec wiring + Fake / real LLM |
| `run_demo.py` | CLI |

## Relation to G5 voice hub

G5 (`voice_hub_test.py`) already does intent classify → route → subprocess
skills in one script. This example lifts **classify + decide + record +
approval** into ADP, and keeps Act behind a bridge so the same platform path
used for aquaculture can be demonstrated on robotics. Scenario C mirrors the
stationary / motion VLM test scripts rather than the full voice hub search path.
