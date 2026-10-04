import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "watch_cycles.sh"


def run_watcher(tmp_path, fail_command, fail_attempt):
    for command in ("git", "python"):
        mock=tmp_path/command
        mock.write_text(
            '#!/usr/bin/env bash\n'
            f'counter="{tmp_path}/{command}.count"\n'
            'n=0; if [[ -f "$counter" ]]; then read -r n < "$counter"; fi\n'
            'n=$((n+1)); echo "$n" > "$counter"\n'
            f'if [[ "{command}" == "{fail_command}" && "$n" == "{fail_attempt}" ]]; then exit 1; fi\n'
        )
        mock.chmod(0o755)
    env={**os.environ,"PATH":str(tmp_path)+os.pathsep+os.environ["PATH"],
         "PULSO_WATCH_ATTEMPTS":"3","PULSO_WATCH_DELAY_SECONDS":"0"}
    return subprocess.run(["bash",str(SCRIPT)],env=env,text=True,capture_output=True,timeout=10)


def test_prediction_failure_does_not_stop_future_queries(tmp_path):
    result=run_watcher(tmp_path,"python",1)
    assert result.returncode == 0
    assert (tmp_path/"python.count").read_text().strip() == "3"
    assert "warning" in result.stdout


def test_git_failure_skips_submission_and_recovers(tmp_path):
    result=run_watcher(tmp_path,"git",1)
    assert result.returncode == 0
    assert (tmp_path/"python.count").read_text().strip() == "2"


def test_unrecovered_final_failure_is_reported(tmp_path):
    result=run_watcher(tmp_path,"python",3)
    assert result.returncode == 1
    assert "::error::" in result.stdout
