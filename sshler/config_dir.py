from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir

ENV_CONFIG_DIR = "SSHLER_CONFIG_DIR"


def get_config_dir() -> Path:
    """Return the configuration directory, creating it when missing.

    English:
        Determines the directory that will contain ``boxes.yaml`` and creates
        it if necessary.

    日本語:
        ``boxes.yaml`` を格納するディレクトリを決定し、存在しない場合は作成します。
    """

    override_directory = os.getenv(ENV_CONFIG_DIR)
    if override_directory:
        configuration_dir = Path(override_directory).expanduser()
    else:
        configuration_dir = Path(user_config_dir(appname="sshler", appauthor=False))
    configuration_dir.mkdir(parents=True, exist_ok=True)
    return configuration_dir
