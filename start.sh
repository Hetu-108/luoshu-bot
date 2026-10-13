#!/usr/bin/env bash
# ============================================
#  洛书 bot —— Linux 启动脚本
# ============================================
# 用法：
#   chmod +x start.sh && ./start.sh
#
# 启动前请确认：
#   1. 已运行登录程序（提供 OneBot 连接，见 .env 的 HOST/PORT）
#   2. 已运行 Ollama（本地大模型，默认 http://127.0.0.1:11434）
#
# 提示：
#   - 想后台常驻并开机自启，请用 systemd（见 luoshu.service.example）
#   - 想临时后台运行：nohup ./start.sh > luoshu.log 2>&1 &

set -e

# 解析脚本所在目录（兼容没有 readlink -f 的系统）
_self="$0"
if command -v readlink >/dev/null 2>&1 && readlink -f "$_self" >/dev/null 2>&1; then
    _dir="$(dirname "$(readlink -f "$_self")")"
else
    _dir="$(cd "$(dirname "$_self")" && pwd)"
fi
cd "$_dir"

echo "================================"
echo "       洛书bot 启动器"
echo "================================"
echo
echo "[提示] 请先确认已运行登录程序 和 Ollama"
echo

if [ -x ".venv/bin/python" ]; then
    exec ./.venv/bin/python bot.py
else
    echo "[错误] 未找到虚拟环境，或它不是真正的 Linux 环境：.venv/bin/python"
    echo "       请先运行： ./install.sh"
    echo "       （若 .venv 是在 Windows 下创建的，需删除后重新在 Linux 上安装）"
    exit 1
fi
