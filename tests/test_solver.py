"""Solver orchestration + in-container driver tests.

Two layers, neither of which needs Docker:

- The driver's *pure* pieces (options-XML builders, variables→rows, CSV write)
  are loaded straight from `docker/solve_lap.py` by file path — that module
  guards `import fastest_lap` so it stays importable off-container. Its native
  CSV output is round-tripped through the real `import-lap` consumer.
- The host orchestration's Docker command builders and `solve_lap` sequencing
  are pinned with an injected recording runner.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from twinkly_mockup.cli import app
from twinkly_mockup.import_lap import load_native_lap
from twinkly_mockup.solver import (
    BASE_IMAGE_TAG,
    circuit_xml_name,
    NATIVE_CSV_NAME,
    RUN_IMAGE_TAG,
    SolverConfig,
    SolverError,
    build_base_image_cmd,
    build_run_image_cmd,
    compile_cmd,
    driver_cmd,
    load_solver_config,
    solve_lap,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRIVER_PATH = _REPO_ROOT / "docker" / "solve_lap.py"


def _load_driver() -> ModuleType:
    spec = importlib.util.spec_from_file_location("solve_lap_driver", _DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


# --- driver: options XML -----------------------------------------------------


def test_preprocessor_options_is_wellformed_and_carries_inputs() -> None:
    xml = driver.build_preprocessor_options(
        "/config/left.kml",
        "/config/right.kml",
        "/out/monaco.xml",
        is_closed=True,
        number_of_elements=750,
        mode="equally-spaced",
    )
    root = ET.fromstring(xml)
    assert root.find("kml_files/left").text == "/config/left.kml"
    assert root.find("kml_files/right").text == "/config/right.kml"
    assert root.find("is_closed").text == "true"
    assert root.find("number_of_elements").text == "750"
    assert root.find("mode").text == "equally-spaced"
    assert root.find("xml_file_name").text == "/out/monaco.xml"


def test_preprocessor_options_open_circuit() -> None:
    xml = driver.build_preprocessor_options(
        "/config/l.kml",
        "/config/r.kml",
        "/out/c.xml",
        is_closed=False,
        number_of_elements=500,
        mode="equally-spaced",
    )
    assert ET.fromstring(xml).find("is_closed").text == "false"


def test_optimal_laptime_options_has_run_prefix() -> None:
    root = ET.fromstring(driver.build_optimal_laptime_options())
    assert root.find("output_variables/prefix").text == "run/"


# --- driver: variables -> rows -----------------------------------------------


def _run_dict() -> dict[str, list[float]]:
    return {
        driver.VAR_TIME: [0.0, 0.5, 1.0],
        driver.VAR_X: [10.0, 11.0, 12.0],
        driver.VAR_Y: [-1.0, -2.0, -3.0],
        driver.VAR_YAW: [0.1, 0.2, 0.3],
        "chassis.velocity.x": [80.0, 81.0, 82.0],  # extra channel, ignored
    }


def test_optimal_lap_to_rows_zips_channels_in_order() -> None:
    rows = driver.optimal_lap_to_rows(_run_dict())
    assert rows == [
        (0.0, 10.0, -1.0, 0.1),
        (0.5, 11.0, -2.0, 0.2),
        (1.0, 12.0, -3.0, 0.3),
    ]


def test_optimal_lap_to_rows_missing_channel_raises() -> None:
    run = _run_dict()
    del run[driver.VAR_YAW]
    with pytest.raises(driver.SolverDriverError) as exc:
        driver.optimal_lap_to_rows(run)
    assert "chassis.attitude.yaw" in str(exc.value)


def test_optimal_lap_to_rows_ragged_channels_raise() -> None:
    run = _run_dict()
    run[driver.VAR_X] = run[driver.VAR_X][:-1]
    with pytest.raises(driver.SolverDriverError) as exc:
        driver.optimal_lap_to_rows(run)
    assert "disagree in length" in str(exc.value)


def test_optimal_lap_to_rows_empty_raises() -> None:
    run = {k: [] for k in (driver.VAR_TIME, driver.VAR_X, driver.VAR_Y, driver.VAR_YAW)}
    with pytest.raises(driver.SolverDriverError):
        driver.optimal_lap_to_rows(run)


def test_native_csv_round_trips_through_import_lap_consumer(tmp_path: Path) -> None:
    """The driver's output must satisfy the real `import-lap` input contract."""
    out = tmp_path / "lap.csv"
    driver.write_native_csv(out, driver.optimal_lap_to_rows(_run_dict()))

    time, x, y, yaw = load_native_lap(out)
    assert list(time) == [0.0, 0.5, 1.0]
    assert list(x) == [10.0, 11.0, 12.0]
    assert list(y) == [-1.0, -2.0, -3.0]
    assert list(yaw) == [0.1, 0.2, 0.3]


# --- host: config loading ----------------------------------------------------


def _write_config(dir_: Path, *, vehicle: str = "veh.xml") -> Path:
    (dir_ / "monaco_left.kml").write_text("<kml/>")
    (dir_ / "monaco_right.kml").write_text("<kml/>")
    (dir_ / vehicle).write_text("<vehicle/>")
    path = dir_ / "solver.yaml"
    path.write_text(
        "circuit: monaco\n"
        "left_kml: monaco_left.kml\n"
        "right_kml: monaco_right.kml\n"
        f"vehicle_xml: {vehicle}\n"
        "number_of_elements: 500\n"
        "is_closed: true\n"
        "mode: equally-spaced\n"
    )
    return path


def test_load_solver_config_resolves_paths_against_config_dir(tmp_path: Path) -> None:
    config = load_solver_config(_write_config(tmp_path))
    assert config.left_kml == tmp_path / "monaco_left.kml"
    assert config.vehicle_xml == tmp_path / "veh.xml"
    assert config.number_of_elements == 500


def test_load_solver_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "circuit: monaco\nleft_kml: a\nright_kml: b\nvehicle_xml: c\nbogus: 1\n"
    )
    with pytest.raises(Exception):  # pydantic ValidationError (extra=forbid)
        load_solver_config(path)


# --- host: docker command builders -------------------------------------------


def test_build_image_cmds_tag_and_context() -> None:
    base = build_base_image_cmd(Path("/repo/submodules/fastest-lap"))
    assert base[:4] == ["docker", "build", "-t", BASE_IMAGE_TAG]
    assert base[-1].endswith("submodules/fastest-lap/src/scripts/linux")

    run = build_run_image_cmd(Path("/repo/docker"))
    assert run[:4] == ["docker", "build", "-t", RUN_IMAGE_TAG]
    assert "--build-arg" in run
    assert f"BASE_IMAGE={BASE_IMAGE_TAG}" in run


def test_compile_cmd_mounts_submodule_and_runs_cmake() -> None:
    cmd = compile_cmd(Path("/repo/submodules/fastest-lap"), uid=1000, gid=1000)
    assert cmd[:2] == ["docker", "run"]
    assert "--user" in cmd and "1000:1000" in cmd
    assert cmd[-3] == "sh" and cmd[-2] == "-c"
    assert "PYTHON_API_ABSOLUTE_PATH=off" in cmd[-1]
    assert BASE_IMAGE_TAG in cmd


def _config(tmp_path: Path) -> tuple[SolverConfig, Path]:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "monaco_left.kml").write_text("<kml/>")
    (cfg_dir / "monaco_right.kml").write_text("<kml/>")
    fl_dir = tmp_path / "submodules" / "fastest-lap"
    veh = fl_dir / "database" / "vehicles" / "f1" / "limebeer-2014-f1.xml"
    veh.parent.mkdir(parents=True)
    veh.write_text("<vehicle/>")
    config = SolverConfig(
        circuit="monaco",
        left_kml=cfg_dir / "monaco_left.kml",
        right_kml=cfg_dir / "monaco_right.kml",
        vehicle_xml=veh,
        number_of_elements=750,
    )
    return config, cfg_dir


def test_driver_cmd_translates_host_paths_to_container_mounts(tmp_path: Path) -> None:
    config, cfg_dir = _config(tmp_path)
    fl_dir = tmp_path / "submodules" / "fastest-lap"
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()

    cmd = driver_cmd(
        config,
        cfg_dir,
        out_dir,
        fastest_lap_dir=fl_dir,
        docker_dir=tmp_path / "docker",
    )

    assert RUN_IMAGE_TAG in cmd
    assert "python3" in cmd
    # KMLs cross into /config, vehicle into /fastest-lap, artifacts into /out.
    assert cmd[cmd.index("--left") + 1] == "/config/monaco_left.kml"
    assert cmd[cmd.index("--right") + 1] == "/config/monaco_right.kml"
    vehicle = cmd[cmd.index("--vehicle") + 1]
    assert vehicle == "/fastest-lap/database/vehicles/f1/limebeer-2014-f1.xml"
    assert cmd[cmd.index("--circuit-xml") + 1] == f"/out/{circuit_xml_name(config)}"
    assert cmd[cmd.index("--out-csv") + 1] == f"/out/{NATIVE_CSV_NAME}"
    assert cmd[cmd.index("--n-elements") + 1] == "750"
    assert "--closed" in cmd
    # A read-only config mount and a read-write out mount are both present.
    assert f"{cfg_dir.resolve()}:/config:ro" in cmd
    assert f"{out_dir.resolve()}:/out" in cmd


def test_driver_cmd_runs_from_the_wrapper_dir(tmp_path: Path) -> None:
    """The wrapper resolves its compiled-lib path against the CWD.

    `fastest_lap.py` does `CDLL("../../build/lib/libfastestlapc.so.0.5")`, which
    is relative to the *process* CWD, not the wrapper's file. Running from /out
    resolved that to /build/lib and `import fastest_lap` died with OSError, so
    the working directory is part of the contract, not incidental.
    """
    config, cfg_dir = _config(tmp_path)
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()

    cmd = driver_cmd(
        config,
        cfg_dir,
        out_dir,
        fastest_lap_dir=tmp_path / "submodules" / "fastest-lap",
        docker_dir=tmp_path / "docker",
    )

    assert cmd[cmd.index("-w") + 1] == "/fastest-lap/examples/python"


def test_driver_cmd_open_circuit_flag(tmp_path: Path) -> None:
    config, cfg_dir = _config(tmp_path)
    config = config.model_copy(update={"is_closed": False})
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    cmd = driver_cmd(
        config,
        cfg_dir,
        out_dir,
        fastest_lap_dir=tmp_path / "submodules" / "fastest-lap",
        docker_dir=tmp_path / "docker",
    )
    assert "--no-closed" in cmd and "--closed" not in cmd


def test_driver_cmd_rejects_kml_outside_config_dir(tmp_path: Path) -> None:
    config, cfg_dir = _config(tmp_path)
    stray = tmp_path / "elsewhere.kml"
    stray.write_text("<kml/>")
    config = config.model_copy(update={"left_kml": stray})
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    with pytest.raises(SolverError) as exc:
        driver_cmd(
            config,
            cfg_dir,
            out_dir,
            fastest_lap_dir=tmp_path / "submodules" / "fastest-lap",
            docker_dir=tmp_path / "docker",
        )
    assert "cannot be mounted" in str(exc.value)


# --- host: solve_lap sequencing (injected runner) ----------------------------


def test_solve_lap_runs_four_stages_in_order(tmp_path: Path) -> None:
    config, cfg_dir = _config(tmp_path)
    out_dir = tmp_path / "artifacts"
    calls: list[list[str]] = []

    artifacts = solve_lap(
        config,
        cfg_dir,
        out_dir,
        fastest_lap_dir=tmp_path / "submodules" / "fastest-lap",
        docker_dir=tmp_path / "docker",
        runner=calls.append,
        use_host_user=False,
    )

    # build base, build run, compile, driver.
    assert len(calls) == 4
    assert calls[0][:2] == ["docker", "build"] and BASE_IMAGE_TAG in calls[0]
    assert calls[1][:2] == ["docker", "build"] and RUN_IMAGE_TAG in calls[1]
    assert calls[2][:2] == ["docker", "run"] and "cmake" in " ".join(calls[2])
    assert calls[3][:2] == ["docker", "run"] and "python3" in calls[3]
    assert artifacts.circuit_xml == out_dir / circuit_xml_name(config)
    assert artifacts.native_csv == out_dir / NATIVE_CSV_NAME
    assert out_dir.exists()  # created for the mount


def test_solve_lap_can_skip_build_and_compile(tmp_path: Path) -> None:
    config, cfg_dir = _config(tmp_path)
    calls: list[list[str]] = []
    solve_lap(
        config,
        cfg_dir,
        tmp_path / "artifacts",
        fastest_lap_dir=tmp_path / "submodules" / "fastest-lap",
        docker_dir=tmp_path / "docker",
        runner=calls.append,
        build=False,
        compile=False,
        use_host_user=False,
    )
    assert len(calls) == 1  # driver only
    assert "python3" in calls[0]


# --- CLI ---------------------------------------------------------------------


def test_cli_solve_lap_dry_run_prints_commands(tmp_path: Path) -> None:
    # Use the real repo config: its vehicle_xml lives under the default
    # submodule dir (required by driver_cmd's path translation), and the pending
    # KMLs needn't exist — translation is purely lexical.
    config_path = _REPO_ROOT / "configs" / "monaco_solver.yaml"
    result = CliRunner().invoke(
        app,
        ["solve-lap", "--config", str(config_path), "--out", str(tmp_path / "out"),
         "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "docker build" in result.output
    assert "docker run" in result.output
    assert "python3" in result.output
