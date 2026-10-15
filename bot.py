# python3
# -*- coding: utf-8 -*-
# @File    : bot.py
# @Usage   : 洛书 bot 主程序（Windows / Linux 通用）
#
# 启动前请确认：
#   1. 已运行登录程序（提供 OneBot 连接，Windows 下为 LLNT/Main.exe）
#   2. 已运行 Ollama（本地大模型，用于 AI 对话）
#   3. 已安装依赖（首次运行见 使用说明.txt）
#
# Windows: 双击 启动bot.bat
# Linux  : ./start.sh
import os
import sys
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))

IS_WINDOWS = sys.platform.startswith("win")


def _pause_if_windows() -> None:
    """Windows 下双击运行时暂停窗口，方便看报错；Linux 下不做处理。"""
    if IS_WINDOWS:
        try:
            os.system("pause")
        except Exception:
            pass


try:
    print("Python %s on %s" % (sys.version, sys.platform))

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

    nonebot.init()
    app = nonebot.get_asgi()

    driver = nonebot.get_driver()
    driver.register_adapter(ONEBOT_V11Adapter)

    nonebot.load_builtin_plugins("echo")

    # 加载 src/plugins 下的所有插件（见 pyproject.toml）
    nonebot.load_from_toml("pyproject.toml")
    expected = {p.stem for p in Path("src/plugins").glob("*.py") if not p.name.startswith("_")}
    missing = expected - {p.name for p in nonebot.get_loaded_plugins()}
    if missing:
        raise RuntimeError(f"插件加载失败：{', '.join(sorted(missing))}")

except Exception as e:
    print(f"启动出错，请查看群文件常见问题：{e}")
    _pause_if_windows()
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        # Windows 多进程（spawn）需要显式指定入口模块，否则子进程无法导入 app；
        # Linux 默认 fork，直接传 app 对象即可。
        if IS_WINDOWS:
            nonebot.run(app="__mp_main__:app")
        else:
            nonebot.run(app=app)
    except Exception as e:
        print(f"运行出错，请查看群文件常见问题：{e}")
        _pause_if_windows()
        raise SystemExit(1)

